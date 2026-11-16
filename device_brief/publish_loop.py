#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行情面板设备图的常驻发布循环（在 GitHub Actions 里运行）。

GitHub 的定时任务（schedule）做不到每分钟，在本仓库实际几小时才触发一次。
所以不用定时器，而是让一个任务常驻：

    渲染 → 画面有变化就 commit + push → 有没有后继在排队？→ 睡到下一个周期

- 周期：每 ``--interval`` 秒（默认 60）抓一次行情、渲染一次面板。
- 接力：单个任务最长 6 小时，所以一轮只跑 ``--loop-seconds``（默认 5.5 小时）。
  这一轮正常运行几分钟后，就用 workflow_dispatch 预约好下一轮：工作流里同一分支
  只允许一个循环在跑，预约的运行会一直排队，等这一轮结束——不管是跑满、出错、
  被取消还是执行机故障——才自动开始。所以循环中途意外中断也能在一分钟左右续上，
  不依赖并不准点的定时器。收尾时如果发现没有后继在排队，再补触发一次。
- 让位：同一工作流、同一分支上出现比自己新且尚未结束的运行，而且不是上面那种
  预约的接力（即代码推送、有人手动 Run workflow）时，立刻收尾，新代码最多等
  一个周期就能接管；让位时不再接力。
- 这一轮还没有正常运行够几分钟就退出时不预约后继，由看门狗工作流
  （aibrief-watchdog.yml）重新拉起，避免启动即出错时无限快速自触发。
- 行情不足（渲染器退出码 3）或渲染超时只跳过这一轮；推送被拒绝时把结果叠到
  远端最新提交上重试，仍失败就恢复到远端状态，下一轮重新来过。

- 每分钟一个提交，仓库历史增长很快。``--no-preview`` 让渲染器不出 PNG 预览
  （设备不读它，而它占每次提交体积的一半以上），发布时都带上。

用法：
    # 默认分支：每分钟发布，跑满后接力
    python3 device_brief/publish_loop.py --out-dir aibrief --publish --no-preview
    # 其他分支 / 本地：只渲染一次到指定目录，不提交
    python3 device_brief/publish_loop.py --out-dir /tmp/market-panel --loop-seconds 0
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

DEFAULT_INTERVAL_SECONDS = 60.0
# GitHub 托管的任务最长 6 小时；一轮跑 5.5 小时，余量留给环境准备和收尾。
DEFAULT_LOOP_SECONDS = 330 * 60
MAX_LOOP_SECONDS = 340 * 60
# 每分钟以内的更新对行情接口和仓库历史都是负担，命令行不接受更短的周期。
MIN_INTERVAL_SECONDS = 30.0
# 只有一轮至少跑了这么久才接力：配置写错时不会变成几秒一次的无限自触发。
MIN_RELAY_LOOP_SECONDS = 5 * 60
# 这一轮正常运行了这么久（且至少成功渲染过一次）才预约后继：
# 启动即出错的代码不会变成一分钟一次的无限自触发。
STANDBY_AFTER_SECONDS = 5 * 60
# 预约之后这么久仍然看不到排队的后继（被人取消、被别的运行顶掉）就再预约一次。
STANDBY_RETRY_SECONDS = 15 * 60
# 自动接力和看门狗都用 GITHUB_TOKEN 触发 workflow_dispatch，触发者是 github-actions[bot]。
RELAY_EVENT = "workflow_dispatch"
BOT_LOGIN_SUFFIX = "[bot]"
DEFAULT_MIN_ROWS = 9
DEFAULT_WORKFLOW = "aibrief.yml"
COMMIT_MESSAGE = "Update market panel device image [skip ci]"
COMMIT_USER_NAME = "github-actions[bot]"
COMMIT_USER_EMAIL = "github-actions[bot]@users.noreply.github.com"

RENDER_TIMEOUT_SECONDS = 50
GIT_TIMEOUT_SECONDS = 45
GH_TIMEOUT_SECONDS = 30
PUSH_ATTEMPTS = 3
DISPATCH_ATTEMPTS = 3
DISPATCH_RETRY_SECONDS = 5.0
# 渲染器退出码 3：有效行情不足，保留上一版（见 render_brief.py）。
EXIT_NOT_ENOUGH_ROWS = 3
# 一轮结束时最后这么多次渲染全部出错，才把整轮标成失败（问题仍在持续）。
FAIL_STREAK_LIMIT = 5
# 整轮没有一次渲染成功且至少跑了这么多次，标成失败。
MIN_ITERATIONS_FOR_NO_RENDER_FAILURE = 3
MAX_ANNOTATIONS = 8
SUCCESSOR_PAGE_SIZE = 10

RENDER_OK = "ok"
RENDER_SKIPPED = "skipped"
RENDER_ERROR = "error"

PUBLISH_UNCHANGED = "unchanged"
PUBLISH_PUSHED = "pushed"
PUBLISH_FAILED = "failed"

TZ_CN = ZoneInfo("Asia/Shanghai")


@dataclass
class Config:
    repo_dir: Path
    out_dir: Path
    interval: float = DEFAULT_INTERVAL_SECONDS
    loop_seconds: float = DEFAULT_LOOP_SECONDS
    min_rows: int = DEFAULT_MIN_ROWS
    publish: bool = False
    relay: bool = False
    preview: bool = True
    branch: str = ""
    workflow: str = DEFAULT_WORKFLOW
    repository: str = ""
    run_id: int = 0
    render_cmd: List[str] = field(
        default_factory=lambda: [sys.executable, str(HERE / "render_brief.py")])


@dataclass
class CommandResult:
    code: int
    out: str = ""
    err: str = ""
    timed_out: bool = False

    def last_line(self) -> str:
        for text in (self.err, self.out):
            lines = [line for line in text.strip().splitlines() if line.strip()]
            if lines:
                return lines[-1].strip()
        return ""


@dataclass
class Stats:
    iterations: int = 0
    rendered: int = 0
    pushed: int = 0
    unchanged: int = 0
    skipped: int = 0
    render_errors: int = 0
    push_failures: int = 0
    unexpected_errors: int = 0
    # 结尾连续出错的次数（渲染出错或意外错误）；任何一次正常的渲染都会清零。
    tail_render_errors: int = 0
    yielded: bool = False
    standby_dispatches: int = 0      # 循环中途预约后继的次数
    successor_queued: bool = False   # 收尾时后继已经在排队
    relayed: bool = False            # 收尾时补触发了下一轮
    relay_failed: bool = False
    annotations: int = 0


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def run_command(cmd: List[str], *, cwd: Optional[Path] = None, timeout: float) -> CommandResult:
    """运行一个外部命令；不抛异常，超时和找不到命令都折算成失败结果。"""
    try:
        done = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, timeout=timeout,
            text=True, encoding="utf-8", errors="replace", check=False)
    except subprocess.TimeoutExpired as exc:
        return CommandResult(124, _text(exc.stdout), _text(exc.stderr) or "timed out", True)
    except OSError as exc:
        return CommandResult(127, "", str(exc))
    return CommandResult(done.returncode, done.stdout or "", done.stderr or "")


def log(message: str) -> None:
    stamp = datetime.now(TZ_CN).strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def annotate(stats: Stats, level: str, message: str) -> None:
    """在 Actions 运行摘要里留一条注解；数量有上限，避免刷屏。"""
    log(f"{level.upper()}: {message}")
    if os.environ.get("GITHUB_ACTIONS") != "true" or stats.annotations >= MAX_ANNOTATIONS:
        return
    stats.annotations += 1
    print(f"::{level}::{message}", flush=True)


# ---------------------------------------------------------------- 渲染
def render_once(config: Config) -> Tuple[str, str]:
    """跑一次渲染器；返回 (结果, 一行说明)。"""
    cmd = list(config.render_cmd) + [
        "--out-dir", str(config.out_dir), "--require-live", "--min-rows", str(config.min_rows)]
    if not config.preview:
        cmd.append("--no-preview")
    result = run_command(cmd, cwd=config.repo_dir, timeout=RENDER_TIMEOUT_SECONDS)
    first_line = next((line.strip() for line in result.out.splitlines() if line.strip()), "")
    if result.code == 0:
        return RENDER_OK, first_line
    if result.timed_out:
        return RENDER_SKIPPED, f"渲染超时（{RENDER_TIMEOUT_SECONDS}s）"
    if result.code == EXIT_NOT_ENOUGH_ROWS:
        return RENDER_SKIPPED, result.last_line() or "有效行情不足"
    return RENDER_ERROR, f"渲染器退出码 {result.code}：{result.last_line() or '没有输出'}"


# ---------------------------------------------------------------- 提交与推送
class Publisher:
    """把 out_dir 的变化提交并推送到 origin/<branch>。"""

    def __init__(self, repo_dir: Path, out_dir: Path, branch: str):
        self.repo_dir = repo_dir
        self.out_dir = out_dir
        self.branch = branch
        self.rel = out_dir.resolve().relative_to(repo_dir.resolve()).as_posix()

    def git(self, *args: str) -> CommandResult:
        return run_command(["git", *args], cwd=self.repo_dir, timeout=GIT_TIMEOUT_SECONDS)

    def setup(self) -> None:
        self.git("config", "user.name", COMMIT_USER_NAME)
        self.git("config", "user.email", COMMIT_USER_EMAIL)
        # 触发事件到任务真正开始之间，远端可能已经多了提交：从远端最新状态开始，
        # 第一次推送就不会被拒绝，也不会把已经发布过的画面再提交一遍。
        if not self.sync_to_remote():
            log("同步远端失败，沿用检出时的提交")

    def sync_to_remote(self) -> bool:
        if self.git("fetch", "-q", "--depth=1", "origin", self.branch).code != 0:
            return False
        return self.git("reset", "-q", "--hard", "FETCH_HEAD").code == 0

    def _stage(self) -> Optional[bool]:
        """暂存 out_dir；返回是否有变化，None 表示 git 出错。"""
        if self.git("add", "-A", "--", self.rel).code != 0:
            return None
        diff = self.git("diff", "--cached", "--quiet", "--", self.rel)
        if diff.code == 0:
            return False
        return True if diff.code == 1 else None

    def _head(self) -> str:
        return self.git("rev-parse", "HEAD").out.strip()

    def publish(self) -> Tuple[str, str]:
        staged = self._stage()
        if staged is None:
            return PUBLISH_FAILED, "git add / diff 失败"
        if not staged:
            return PUBLISH_UNCHANGED, ""

        base = self._head()
        snapshot = Path(tempfile.mkdtemp(prefix="market-panel-"))
        detail = ""
        try:
            shutil.copytree(self.out_dir, snapshot / "out")
            for attempt in range(1, PUSH_ATTEMPTS + 1):
                commit = self.git("commit", "-q", "-m", COMMIT_MESSAGE)
                if commit.code != 0:
                    detail = f"git commit 失败：{commit.last_line()}"
                    break
                push = self.git("push", "-q", "origin", f"HEAD:{self.branch}")
                if push.code == 0:
                    return PUBLISH_PUSHED, self._head()[:8]
                detail = f"git push 失败：{push.last_line()}"
                # 远端先有了别的提交（OTA 清单、代码合并）：把这次的画面叠到最新提交上重试。
                if not self.sync_to_remote():
                    detail += "；同步远端失败"
                    break
                base = self._head()
                shutil.rmtree(self.out_dir, ignore_errors=True)
                shutil.copytree(snapshot / "out", self.out_dir)
                staged = self._stage()
                if staged is None:
                    detail = "git add / diff 失败"
                    break
                if not staged:
                    return PUBLISH_UNCHANGED, "远端已是同样内容"
                if attempt < PUSH_ATTEMPTS:
                    log(f"推送被拒绝，叠到远端最新提交后重试（{attempt}/{PUSH_ATTEMPTS}）")
            # 放弃这一轮：回到远端状态。磁盘上的设备图随之变回已发布的那一版，
            # 下一轮渲染会发现「有变化」并重新提交，不会留下只存在本地的提交。
            self.git("reset", "-q", "--hard", base)
            return PUBLISH_FAILED, detail or "git push 失败"
        finally:
            shutil.rmtree(snapshot, ignore_errors=True)


# ---------------------------------------------------------------- 后继与接力
def gh_available(config: Config) -> bool:
    return bool(config.repository and config.branch and config.workflow)


def relay_enabled(config: Config) -> bool:
    return (config.relay and gh_available(config)
            and config.loop_seconds >= MIN_RELAY_LOOP_SECONDS)


@dataclass
class Successors:
    """同一工作流、同一分支上比自己新且尚未结束的运行。"""
    takeover: bool = False   # 代码推送 / 有人手动触发：要立刻让位
    standby: bool = False    # 预约的接力：排队等这一轮结束后自动开始，不用让位

    @property
    def any(self) -> bool:
        return self.takeover or self.standby


def is_standby_run(item: dict) -> bool:
    """是不是自动接力（或看门狗）用 GITHUB_TOKEN 触发的运行。"""
    if item.get("event") != RELAY_EVENT:
        return False
    for key in ("triggering_actor", "actor"):
        login = (item.get(key) or {}).get("login")
        if login:
            return str(login).endswith(BOT_LOGIN_SUFFIX)
    return False


def find_successors(config: Config) -> Optional[Successors]:
    """查询比自己新且尚未结束的运行；查询失败返回 None。"""
    if not gh_available(config) or config.run_id <= 0:
        return Successors()
    path = (f"repos/{config.repository}/actions/workflows/{config.workflow}/runs"
            f"?branch={quote(config.branch, safe='')}&per_page={SUCCESSOR_PAGE_SIZE}")
    result = run_command(["gh", "api", path], timeout=GH_TIMEOUT_SECONDS)
    if result.code != 0:
        return None
    found = Successors()
    try:
        for item in json.loads(result.out).get("workflow_runs", []):
            if int(item.get("id", 0)) <= config.run_id or item.get("status") == "completed":
                continue
            if is_standby_run(item):
                found.standby = True
            else:
                found.takeover = True
    except (ValueError, TypeError, AttributeError):
        return None
    return found


def dispatch_once(config: Config) -> CommandResult:
    """触发下一轮循环（workflow_dispatch 不受 GITHUB_TOKEN 不能触发工作流的限制）。"""
    return run_command(
        ["gh", "workflow", "run", config.workflow, "--repo", config.repository,
         "--ref", config.branch, "-f", "relay=true"], timeout=GH_TIMEOUT_SECONDS)


def dispatch_next(config: Config, sleep: Callable[[float], None]) -> bool:
    """收尾时补触发下一轮，失败重试几次。"""
    for attempt in range(1, DISPATCH_ATTEMPTS + 1):
        result = dispatch_once(config)
        if result.code == 0:
            return True
        log(f"触发下一轮失败（{attempt}/{DISPATCH_ATTEMPTS}）：{result.last_line()}")
        if attempt < DISPATCH_ATTEMPTS:
            sleep(DISPATCH_RETRY_SECONDS * attempt)
    return False


# ---------------------------------------------------------------- 主循环
def run_iteration(config: Config, publisher: Optional[Publisher], stats: Stats) -> None:
    stats.iterations += 1
    outcome, summary = render_once(config)
    if outcome == RENDER_ERROR:
        stats.render_errors += 1
        stats.tail_render_errors += 1
        annotate(stats, "warning", summary)
        return
    stats.tail_render_errors = 0
    if outcome == RENDER_SKIPPED:
        stats.skipped += 1
        log(f"跳过：{summary}")
        return

    stats.rendered += 1
    if publisher is None:
        log(f"已渲染（不发布）：{summary}")
        return
    status, detail = publisher.publish()
    if status == PUBLISH_PUSHED:
        stats.pushed += 1
        log(f"已发布 {detail}：{summary}")
    elif status == PUBLISH_UNCHANGED:
        stats.unchanged += 1
        log(f"无变化{('（' + detail + '）') if detail else ''}：{summary}")
    else:
        stats.push_failures += 1
        annotate(stats, "warning", f"发布失败，下一轮重试：{detail}")


def run_loop(config: Config,
             clock: Callable[[], float] = time.monotonic,
             sleep: Callable[[float], None] = time.sleep) -> Stats:
    stats = Stats()
    publisher = None
    if config.publish:
        publisher = Publisher(config.repo_dir, config.out_dir, config.branch)
        publisher.setup()

    started = clock()
    deadline = started + config.loop_seconds
    slot = 0
    standby_at: Optional[float] = None
    while True:
        try:
            run_iteration(config, publisher, stats)
        except Exception as exc:  # noqa: BLE001 - 常驻循环不能因为一次意外就整轮停更
            stats.unexpected_errors += 1
            stats.tail_render_errors += 1
            annotate(stats, "warning", f"本周期出现意外错误，下一周期重试：{type(exc).__name__}: {exc}")
            if publisher is not None:
                # 可能停在「已提交、未推送」的中间状态：回到远端状态，下一周期重新来过。
                publisher.sync_to_remote()
        found = find_successors(config)
        if found is not None and found.takeover:
            stats.yielded = True
            log("发现新的运行在排队（代码推送或手动触发），收尾让位")
            break
        now = clock()
        if (relay_enabled(config) and config.run_id > 0 and found is not None
                and not found.standby and stats.rendered > 0
                and now - started >= STANDBY_AFTER_SECONDS
                and (standby_at is None or now - standby_at >= STANDBY_RETRY_SECONDS)):
            result = dispatch_once(config)
            if result.code == 0:
                standby_at = now
                stats.standby_dispatches += 1
                log("已预约下一轮：它会排队等这一轮结束（跑满、出错或被取消）后自动开始")
            else:
                log(f"预约下一轮失败，下一周期重试：{result.last_line()}")
        # 一次渲染拖过了一个或多个周期时，直接对齐到下一个周期，不补跑。
        slot = max(slot + 1, int((now - started) // config.interval) + 1)
        next_tick = started + slot * config.interval
        if next_tick >= deadline:
            break
        sleep(max(0.0, next_tick - now))

    if relay_enabled(config) and not stats.yielded:
        found = find_successors(config)
        if found is not None and found.any:
            stats.successor_queued = True
            log("后继运行已在排队，这一轮结束后自动开始")
        elif dispatch_next(config, sleep):
            stats.relayed = True
            log("已触发下一轮循环")
        else:
            stats.relay_failed = True
            annotate(stats, "error", "触发下一轮循环失败，等待看门狗重新拉起")
    return stats


def exit_code(stats: Stats) -> int:
    if stats.relay_failed:
        return 1
    if stats.iterations and stats.tail_render_errors >= min(FAIL_STREAK_LIMIT, stats.iterations):
        return 1
    if stats.iterations >= MIN_ITERATIONS_FOR_NO_RENDER_FAILURE and stats.rendered == 0:
        return 1
    return 0


def summary_text(stats: Stats) -> str:
    if stats.yielded:
        ending = "让位给新的运行"
    elif stats.successor_queued:
        ending = "后继已在排队"
    elif stats.relayed:
        ending = "已触发下一轮"
    elif stats.relay_failed:
        ending = "接力失败"
    else:
        ending = "正常结束"
    unexpected = f"，意外错误 {stats.unexpected_errors}" if stats.unexpected_errors else ""
    published = stats.pushed + stats.unchanged + stats.push_failures
    if stats.rendered and not published:
        rendered = f"渲染成功 {stats.rendered}（不发布）"
    else:
        rendered = (f"发布 {stats.pushed}，无变化 {stats.unchanged}，"
                    f"发布失败 {stats.push_failures}")
    return (f"共 {stats.iterations} 个周期：{rendered}，行情不足或超时 {stats.skipped}，"
            f"渲染出错 {stats.render_errors}{unexpected}；{ending}")


def write_step_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"行情面板发布循环：{text}\n")
    except OSError:
        pass


def parse_args(argv: Optional[List[str]]) -> Config:
    parser = argparse.ArgumentParser(description="行情面板设备图的常驻发布循环")
    parser.add_argument("--out-dir", default="aibrief",
                        help="输出目录；--publish 时必须在仓库内（默认 aibrief）")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS,
                        help=f"两次更新的间隔秒数（默认 {DEFAULT_INTERVAL_SECONDS:.0f}，"
                             f"不小于 {MIN_INTERVAL_SECONDS:.0f}）")
    parser.add_argument("--loop-seconds", type=float, default=DEFAULT_LOOP_SECONDS,
                        help=f"本轮循环时长（默认 {DEFAULT_LOOP_SECONDS}）；0 表示只渲染一次")
    parser.add_argument("--min-rows", type=int, default=DEFAULT_MIN_ROWS,
                        help=f"至少多少行有最新点位才更新（默认 {DEFAULT_MIN_ROWS}）")
    parser.add_argument("--publish", action="store_true",
                        help="把变化提交并推送到 origin/<branch>，跑满时长后触发下一轮")
    parser.add_argument("--no-relay", action="store_true", help="发布但不触发下一轮")
    parser.add_argument("--no-preview", action="store_true",
                        help="不生成 latest.png 预览并删掉已有的；每分钟发布时带上，"
                             "PNG 占提交体积的一半以上而设备不读它")
    parser.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME", ""))
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--run-id", type=int, default=int(os.environ.get("GITHUB_RUN_ID") or 0))
    args = parser.parse_args(argv)

    if args.interval < MIN_INTERVAL_SECONDS:
        parser.error(f"--interval 不能小于 {MIN_INTERVAL_SECONDS:.0f} 秒")
    if not 0 <= args.loop_seconds <= MAX_LOOP_SECONDS:
        parser.error(f"--loop-seconds 必须在 0..{MAX_LOOP_SECONDS} 之间（单个任务上限 6 小时）")
    if not 1 <= args.min_rows <= 12:
        parser.error("--min-rows 必须在 1..12 之间")
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    if args.publish:
        if os.environ.get("GITHUB_ACTIONS") != "true":
            parser.error("--publish 只能在 GitHub Actions 里使用：它会把工作区重置到远端最新提交")
        if not args.branch:
            parser.error("--publish 需要 --branch（或环境变量 GITHUB_REF_NAME）")
        try:
            out_dir.resolve().relative_to(ROOT.resolve())
        except ValueError:
            parser.error("--publish 时 --out-dir 必须在仓库内")
    return Config(
        repo_dir=ROOT, out_dir=out_dir, interval=args.interval, loop_seconds=args.loop_seconds,
        min_rows=args.min_rows, publish=args.publish, relay=args.publish and not args.no_relay,
        preview=not args.no_preview,
        branch=args.branch, workflow=args.workflow, repository=args.repository,
        run_id=args.run_id)


def main(argv: Optional[List[str]] = None) -> int:
    config = parse_args(argv)
    mode = "发布" if config.publish else "只渲染"
    log(f"开始：{mode}，间隔 {config.interval:.0f}s，时长 {config.loop_seconds:.0f}s，"
        f"分支 {config.branch or '-'}，输出 {config.out_dir}")
    stats = run_loop(config)
    text = summary_text(stats)
    log(text)
    write_step_summary(text)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # 每轮一条，显示在运行摘要页顶部：不用翻日志就能看到这一轮发布了多少次、怎么结束的。
        print(f"::notice title=行情面板发布循环::{text}", flush=True)
    code = exit_code(stats)
    if code == 0 and stats.rendered == 0:
        annotate(stats, "warning", "本轮没有取到足够的行情，设备图保持上一版")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
