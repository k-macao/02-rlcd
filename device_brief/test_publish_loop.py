#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行情面板常驻发布循环（publish_loop.py）的回归测试。

    python3 device_brief/test_publish_loop.py

不联网、不碰真实仓库：每个测试在临时目录里建一个裸仓库当远端、一个克隆当工作区，
渲染器换成按脚本写文件的替身，`gh` 换成 PATH 里的假命令。回归重点：
- 画面有变化才提交；推送被拒绝时叠到远端最新提交上重试，失败后不留下本地提交；
- 每个周期一次，拖慢时不补跑；正常运行几分钟后预约后继，收尾时没有后继才补触发；
  有代码推送或手动触发的新运行在排队就让位，预约的接力不用让位；
- 行情不足只跳过；渲染持续出错或整轮没有数据时把这一轮标成失败；
- 工作流文件与脚本的参数、时长、权限相互一致。
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import publish_loop  # noqa: E402
import render_brief  # noqa: E402

ROOT = publish_loop.ROOT
WORKFLOWS = ROOT / ".github" / "workflows"

STUB_RENDERER = r'''#!/usr/bin/env python3
"""渲染器替身：按 steps.json 里的脚本逐次执行。"""
import json, os, subprocess, sys, time
from pathlib import Path

state = Path(os.environ["STUB_STATE_DIR"])
steps = json.loads((state / "steps.json").read_text(encoding="utf-8"))
counter = state / "render_count"
count = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(count + 1))
with (state / "render_args.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\n")
step = steps[min(count, len(steps) - 1)]
if step.get("hook"):
    subprocess.run(step["hook"], shell=True, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
if step.get("sleep"):
    time.sleep(step["sleep"])
out_dir = Path(sys.argv[sys.argv.index("--out-dir") + 1])
print(f"行情采集完成：{step.get('rows', 12)}/12 行有效（第 {count + 1} 次）")
code = step.get("exit", 0)
if code == 0:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("latest.bin", "version.txt"):
        path = out_dir / name
        text = f"{name}:{step['content']}\n"
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
    for name in step.get("remove", []):
        target = out_dir / name
        if target.exists():
            target.unlink()
else:
    print(step.get("message", "stub failure"), file=sys.stderr)
sys.exit(code)
'''

FAKE_GH = r'''#!/usr/bin/env python3
"""假的 gh：记录调用，按 gh_api.json / gh_dispatch.json 里的脚本应答。"""
import json, os, subprocess, sys
from pathlib import Path

state = Path(os.environ["STUB_STATE_DIR"])
args = sys.argv[1:]
with (state / "gh_calls.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")


def scripted(name, default):
    path = state / name
    if not path.exists():
        return default
    items = json.loads(path.read_text(encoding="utf-8"))
    counter = state / (name + ".count")
    index = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(index + 1))
    return items[min(index, len(items) - 1)] if items else default


standby_file = state / "standby_runs.json"
standby_runs = json.loads(standby_file.read_text(encoding="utf-8")) if standby_file.exists() else []

if args[:1] == ["api"]:
    # 没有写脚本时：之前触发成功的接力运行都在排队（GitHub 上同一分支只允许一个循环在跑）。
    response = scripted("gh_api.json", {"runs": standby_runs})
    if response.get("exit"):
        sys.stderr.write("HTTP 502: Bad Gateway\n")
        sys.exit(response["exit"])
    if "raw" in response:
        sys.stdout.write(response["raw"])
        sys.exit(0)
    payload = json.dumps({"total_count": len(response["runs"]),
                          "workflow_runs": response["runs"]})
    if "--jq" in args:
        # 真正的 gh 内置 jq；这里交给系统的 jq 执行同一个表达式。
        done = subprocess.run(["jq", "-r", args[args.index("--jq") + 1]], input=payload,
                              capture_output=True, text=True)
        sys.stdout.write(done.stdout)
        sys.stderr.write(done.stderr)
        sys.exit(done.returncode)
    print(payload)
    sys.exit(0)
if args[:2] == ["workflow", "run"]:
    code = scripted("gh_dispatch.json", 0)
    if code:
        sys.stderr.write("could not create workflow dispatch event\n")
    else:
        standby_runs.append({"id": 100000 + len(standby_runs), "status": "pending",
                             "event": "workflow_dispatch",
                             "actor": {"login": "github-actions[bot]"},
                             "triggering_actor": {"login": "github-actions[bot]"}})
        standby_file.write_text(json.dumps(standby_runs), encoding="utf-8")
    sys.exit(code)
sys.exit(2)
'''

GIT_IDENTITY = ["-c", "user.name=tester", "-c", "user.email=tester@example.invalid"]


class FakeClock:
    """只在 sleep 时前进的时钟：循环次数不受机器快慢影响。"""

    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {done.stderr}")
    return done.stdout.strip()


class LoopTestCase(unittest.TestCase):
    RUN_ID = 100

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.origin = self.tmp / "origin.git"
        self.work = self.tmp / "work"
        self.other = self.tmp / "other"
        self.url = self.origin.as_uri()

        git(self.tmp, "init", "-q", "--bare", "--initial-branch=main", str(self.origin))
        git(self.tmp, "clone", "-q", self.url, str(self.work))
        (self.work / "aibrief").mkdir()
        (self.work / "aibrief" / "latest.bin").write_text("latest.bin:initial\n", encoding="utf-8")
        (self.work / "aibrief" / "version.txt").write_text("version.txt:initial\n",
                                                          encoding="utf-8")
        (self.work / "aibrief" / "page_01.png").write_text("old page\n", encoding="utf-8")
        (self.work / "README.md").write_text("readme\n", encoding="utf-8")
        git(self.work, "checkout", "-q", "-B", "main")
        git(self.work, "add", "-A")
        git(self.work, *GIT_IDENTITY, "commit", "-q", "-m", "initial")
        git(self.work, "push", "-q", "origin", "HEAD:main")
        # 另一个克隆：模拟别的工作流（发版、代码合并）往远端推提交。
        git(self.tmp, "clone", "-q", self.url, str(self.other))

        self.stub = self.tmp / "stub_render.py"
        self.stub.write_text(STUB_RENDERER, encoding="utf-8")
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        patcher = mock.patch.dict(os.environ, {
            "STUB_STATE_DIR": str(self.state),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("GITHUB_ACTIONS", "GITHUB_STEP_SUMMARY"):
            os.environ.pop(name, None)
        self.clock = FakeClock()

    # ---- 脚本化输入
    def steps(self, *steps: dict) -> None:
        (self.state / "steps.json").write_text(json.dumps(list(steps)), encoding="utf-8")

    def api_responses(self, *responses: dict) -> None:
        (self.state / "gh_api.json").write_text(json.dumps(list(responses)), encoding="utf-8")

    def dispatch_codes(self, *codes: int) -> None:
        (self.state / "gh_dispatch.json").write_text(json.dumps(list(codes)), encoding="utf-8")

    def other_push_command(self, filename: str, content: str, message: str = "other") -> str:
        """一条 shell 命令：让另一个克隆在远端最新提交上加一个提交并推送。"""
        identity = " ".join(GIT_IDENTITY)
        return (f"cd '{self.other}' && git pull -q origin main && mkdir -p \"$(dirname '{filename}')\""
                f" && printf '%s\\n' '{content}' > '{filename}' && git add -A"
                f" && git {identity} commit -q -m '{message}' && git push -q origin HEAD:main")

    # ---- 运行与观察
    def config(self, **overrides) -> publish_loop.Config:
        values = dict(
            repo_dir=self.work, out_dir=self.work / "aibrief", interval=60.0,
            loop_seconds=300.0, publish=True, relay=True, branch="main",
            repository="owner/repo", run_id=self.RUN_ID,
            render_cmd=[sys.executable, str(self.stub)])
        values.update(overrides)
        return publish_loop.Config(**values)

    def run_loop(self, **overrides) -> publish_loop.Stats:
        with redirect_stdout(io.StringIO()) as self.output:
            return publish_loop.run_loop(self.config(**overrides), clock=self.clock.clock,
                                         sleep=self.clock.sleep)

    def render_count(self) -> int:
        path = self.state / "render_count"
        return int(path.read_text()) if path.exists() else 0

    def gh_calls(self) -> list[list[str]]:
        path = self.state / "gh_calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def dispatch_calls(self) -> list[list[str]]:
        return [call for call in self.gh_calls() if call[:2] == ["workflow", "run"]]

    def origin_log(self) -> list[str]:
        return git(self.origin, "log", "--format=%s", "main").splitlines()

    def origin_file(self, path: str) -> str:
        return git(self.origin, "show", f"main:{path}")

    def origin_files(self) -> list[str]:
        return git(self.origin, "ls-tree", "-r", "--name-only", "main").splitlines()


class PublishTest(LoopTestCase):
    def test_commits_only_when_the_image_changes(self):
        self.steps({"content": "A"}, {"content": "A"}, {"content": "B"}, {"content": "B"})
        stats = self.run_loop(loop_seconds=240.0, relay=False)
        self.assertEqual(self.render_count(), 4)
        self.assertEqual((stats.pushed, stats.unchanged), (2, 2))
        self.assertEqual(self.origin_log(),
                         [publish_loop.COMMIT_MESSAGE, publish_loop.COMMIT_MESSAGE, "initial"])
        self.assertEqual(self.origin_file("aibrief/latest.bin"), "latest.bin:B")
        self.assertEqual(self.origin_file("aibrief/version.txt"), "version.txt:B")
        # 本地与远端一致，没有留下未推送的提交。
        self.assertEqual(git(self.work, "rev-parse", "HEAD"),
                         git(self.origin, "rev-parse", "main"))
        self.assertEqual(git(self.work, "status", "--porcelain"), "")

    def test_commit_message_skips_ci_and_uses_the_bot_identity(self):
        self.steps({"content": "A"})
        self.run_loop(loop_seconds=0.0)
        self.assertIn("[skip ci]", publish_loop.COMMIT_MESSAGE)
        self.assertEqual(git(self.origin, "log", "-1", "--format=%an <%ae>", "main"),
                         "github-actions[bot] <github-actions[bot]@users.noreply.github.com>")

    def test_only_the_output_directory_is_committed(self):
        self.steps({"content": "A", "remove": ["page_01.png"]})
        (self.work / "untracked.txt").write_text("scratch\n", encoding="utf-8")
        self.run_loop(loop_seconds=0.0)
        self.assertEqual(self.origin_files(),
                         ["README.md", "aibrief/latest.bin", "aibrief/version.txt"])

    def test_starts_from_the_latest_remote_commit(self):
        # 触发之后、任务开始之前远端已经发布了同样的画面：不重复提交。
        subprocess.run(self.other_push_command("aibrief/latest.bin", "latest.bin:A"),
                       shell=True, check=True)
        subprocess.run(self.other_push_command("aibrief/version.txt", "version.txt:A"),
                       shell=True, check=True)
        self.steps({"content": "A"})
        stats = self.run_loop(loop_seconds=0.0)
        self.assertEqual((stats.pushed, stats.unchanged), (0, 1))
        self.assertEqual(self.origin_log(), ["other", "other", "initial"])
        self.assertEqual(git(self.work, "rev-parse", "HEAD"),
                         git(self.origin, "rev-parse", "main"))
        # 是一开始就对齐了远端，而不是提交后被拒绝才发现内容相同。
        self.assertNotIn("远端已是同样内容", self.output.getvalue())

    def test_rejected_push_is_replayed_on_the_latest_remote_commit(self):
        # 渲染期间别的工作流往远端推了提交：第一次推送被拒绝，叠上去重试。
        self.steps({"content": "A",
                    "hook": self.other_push_command("firmware/latest.json", "v9.9.9", "ota")})
        stats = self.run_loop(loop_seconds=0.0)
        self.assertEqual((stats.pushed, stats.push_failures), (1, 0))
        self.assertEqual(self.origin_log(), [publish_loop.COMMIT_MESSAGE, "ota", "initial"])
        self.assertEqual(self.origin_file("firmware/latest.json"), "v9.9.9")
        self.assertEqual(self.origin_file("aibrief/latest.bin"), "latest.bin:A")
        self.assertIn("叠到远端最新提交后重试", self.output.getvalue())

    def test_same_image_already_on_the_remote_is_not_committed_again(self):
        hook = (self.other_push_command("aibrief/latest.bin", "latest.bin:A", "same") + " && "
                + self.other_push_command("aibrief/version.txt", "version.txt:A", "same"))
        self.steps({"content": "A", "hook": hook})
        stats = self.run_loop(loop_seconds=0.0)
        self.assertEqual((stats.pushed, stats.unchanged, stats.push_failures), (0, 1, 0))
        self.assertEqual(self.origin_log(), ["same", "same", "initial"])
        self.assertEqual(git(self.work, "status", "--porcelain"), "")

    def test_failed_push_leaves_no_local_commit_and_retries_next_cycle(self):
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'remote: denied' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        # 第一个周期远端拒收所有推送；第二个周期开始前恢复正常，画面没有再变。
        self.steps({"content": "A"}, {"content": "A", "hook": f"rm -f '{hook}'"})
        stats = self.run_loop(loop_seconds=120.0, relay=False)
        self.assertEqual(self.render_count(), 2)
        self.assertEqual((stats.push_failures, stats.pushed), (1, 1))
        self.assertEqual(self.origin_log(), [publish_loop.COMMIT_MESSAGE, "initial"])
        self.assertEqual(self.origin_file("aibrief/latest.bin"), "latest.bin:A")
        self.assertEqual(git(self.work, "rev-parse", "HEAD"),
                         git(self.origin, "rev-parse", "main"))
        self.assertIn("发布失败，下一周期重试", self.output.getvalue())

    def test_unreachable_remote_leaves_no_local_commit_and_recovers(self):
        away = self.tmp / "origin.away"
        # 第一个周期远端完全连不上（推送和随后的同步都失败）；第二个周期恢复，画面没有再变。
        # 如果失败后把提交留在本地，第二个周期会认为「无变化」，这一版就永远发不出去。
        self.steps({"content": "A", "hook": f"mv '{self.origin}' '{away}'"},
                   {"content": "A", "hook": f"mv '{away}' '{self.origin}'"})
        stats = self.run_loop(loop_seconds=120.0, relay=False)
        self.assertEqual((stats.push_failures, stats.pushed), (1, 1))
        self.assertEqual(self.origin_log(), [publish_loop.COMMIT_MESSAGE, "initial"])
        self.assertEqual(self.origin_file("aibrief/latest.bin"), "latest.bin:A")
        self.assertEqual(git(self.work, "rev-parse", "HEAD"),
                         git(self.origin, "rev-parse", "main"))
        self.assertEqual(git(self.work, "status", "--porcelain"), "")

    def test_without_publish_nothing_is_committed(self):
        preview = self.tmp / "preview"
        self.steps({"content": "A"})
        stats = self.run_loop(loop_seconds=0.0, publish=False, relay=False, out_dir=preview)
        self.assertEqual(stats.rendered, 1)
        self.assertEqual((preview / "latest.bin").read_text(encoding="utf-8"), "latest.bin:A\n")
        self.assertEqual(self.origin_log(), ["initial"])
        self.assertEqual(git(self.work, "status", "--porcelain"), "")

    def test_renderer_is_called_with_its_real_options(self):
        self.steps({"content": "A"})
        self.run_loop(loop_seconds=0.0, min_rows=7)
        args = json.loads((self.state / "render_args.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(args, ["--out-dir", str(self.work / "aibrief"), "--require-live",
                                "--min-rows", "7"])
        # 这些参数渲染器都认识。
        with redirect_stdout(io.StringIO()) as usage, self.assertRaises(SystemExit):
            render_brief.main(["--help"])
        for option in ("--out-dir", "--require-live", "--min-rows", "--no-preview"):
            self.assertIn(option, usage.getvalue())
        self.assertEqual(publish_loop.EXIT_NOT_ENOUGH_ROWS, 3)
        self.assertEqual(publish_loop.DEFAULT_MIN_ROWS, render_brief.DEFAULT_MIN_ROWS)


    def test_no_preview_is_passed_on_to_the_renderer(self):
        self.steps({"content": "A"})
        self.run_loop(loop_seconds=0.0, preview=False)
        args = json.loads((self.state / "render_args.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(args, ["--out-dir", str(self.work / "aibrief"), "--require-live",
                                "--min-rows", "9", "--no-preview"])

    def test_a_removed_preview_is_committed_even_when_the_image_is_unchanged(self):
        # 换成不带预览的发布后，第一轮即使行情没变也要把仓库里的旧 PNG 删掉。
        self.steps({"content": "initial", "remove": ["page_01.png"]})
        stats = self.run_loop(loop_seconds=0.0, preview=False)
        self.assertEqual(stats.pushed, 1)
        self.assertEqual(self.origin_files(),
                         ["README.md", "aibrief/latest.bin", "aibrief/version.txt"])
        self.assertEqual(self.origin_file("aibrief/latest.bin"), "latest.bin:initial")


class LoopTimingTest(LoopTestCase):
    def test_one_render_per_interval_for_the_whole_loop(self):
        self.steps({"content": "A"})
        stats = self.run_loop(loop_seconds=300.0, relay=False)
        self.assertEqual(self.render_count(), 5)
        self.assertEqual(stats.iterations, 5)
        self.assertEqual(self.clock.sleeps, [60.0] * 4)

    def test_default_loop_updates_every_minute_for_five_and_a_half_hours(self):
        self.assertEqual(publish_loop.DEFAULT_INTERVAL_SECONDS, 60.0)
        self.assertEqual(publish_loop.DEFAULT_LOOP_SECONDS, 330 * 60)
        self.assertLess(publish_loop.MAX_LOOP_SECONDS, 6 * 60 * 60)
        calls = []
        with mock.patch.object(publish_loop, "run_iteration",
                               side_effect=lambda *args: calls.append(self.clock.now)):
            publish_loop.run_loop(self.config(publish=False, relay=False, run_id=0,
                                              loop_seconds=publish_loop.DEFAULT_LOOP_SECONDS),
                                  clock=self.clock.clock, sleep=self.clock.sleep)
        self.assertEqual(len(calls), 330)
        self.assertEqual(calls[1] - calls[0], 60.0)
        self.assertEqual(calls[-1] - calls[0], 329 * 60.0)

    def test_zero_loop_seconds_renders_exactly_once(self):
        self.steps({"content": "A"})
        stats = self.run_loop(loop_seconds=0.0)
        self.assertEqual(self.render_count(), 1)
        self.assertEqual(self.clock.sleeps, [])
        self.assertFalse(stats.relayed)

    def test_slow_cycle_skips_missed_slots_instead_of_catching_up(self):
        durations = iter([130.0, 0.0, 0.0])

        def slow_iteration(_config, _publisher, stats):
            stats.iterations += 1
            stats.rendered += 1
            self.clock.now += next(durations)

        with mock.patch.object(publish_loop, "run_iteration", side_effect=slow_iteration):
            stats = publish_loop.run_loop(
                self.config(publish=False, relay=False, run_id=0, loop_seconds=300.0),
                clock=self.clock.clock, sleep=self.clock.sleep)
        # 第 1 次占掉了 0、60、120 三个周期，下一次对齐到 180，再下一次 240。
        self.assertEqual(stats.iterations, 3)
        self.assertEqual(self.clock.sleeps, [50.0, 60.0])

    def test_render_timeout_only_skips_the_cycle(self):
        self.steps({"content": "A", "sleep": 3}, {"content": "B"})
        with mock.patch.object(publish_loop, "RENDER_TIMEOUT_SECONDS", 0.5):
            stats = self.run_loop(loop_seconds=120.0, relay=False)
        self.assertEqual((stats.skipped, stats.pushed), (1, 1))
        self.assertEqual(publish_loop.exit_code(stats), 0)
        self.assertIn("渲染超时", self.output.getvalue())


class HandoverTest(LoopTestCase):
    HUMAN = {"login": "k-macao"}
    BOT = {"login": "github-actions[bot]"}

    def run_item(self, run_id: int, status: str, event: str = "push", actor: dict | None = None) -> dict:
        actor = self.HUMAN if actor is None else actor
        return {"id": run_id, "status": status, "event": event, "actor": actor,
                "triggering_actor": actor}

    def relay_item(self, run_id: int, status: str = "pending") -> dict:
        return self.run_item(run_id, status, "workflow_dispatch", self.BOT)

    def runs(self, *items) -> dict:
        """(id, status) 表示一次代码推送触发的运行；也可以直接给 run_item / relay_item。"""
        return {"runs": [item if isinstance(item, dict) else self.run_item(*item)
                         for item in items]}

    def api_call_count(self) -> int:
        return sum(1 for call in self.gh_calls() if call[:1] == ["api"])

    def test_yields_to_a_newer_waiting_run_without_relaying(self):
        self.steps({"content": "A"})
        # 后继出现后即使随即消失（例如被人取消），让位的这一轮也不再自己接力。
        self.api_responses(self.runs((101, "pending"), (100, "in_progress")), self.runs())
        stats = self.run_loop()
        self.assertTrue(stats.yielded)
        self.assertEqual(self.render_count(), 1)      # 让位前仍然完成当前这次发布
        self.assertEqual(stats.pushed, 1)
        self.assertEqual(self.dispatch_calls(), [])
        self.assertEqual(len(self.gh_calls()), 1)
        self.assertEqual(publish_loop.exit_code(stats), 0)

    def test_every_unfinished_newer_run_counts_as_a_successor(self):
        for status in ("queued", "pending", "waiting", "requested", "in_progress"):
            self.api_responses(self.runs((self.RUN_ID + 1, status)))
            (self.state / "gh_api.json.count").unlink(missing_ok=True)
            found = publish_loop.find_successors(self.config())
            self.assertEqual((found.takeover, found.standby), (True, False), status)

    def test_older_or_finished_runs_are_not_successors(self):
        self.api_responses(self.runs((150, "completed"), (100, "in_progress"), (99, "in_progress"),
                                     self.relay_item(151, "completed"), self.relay_item(98)))
        self.assertFalse(publish_loop.find_successors(self.config()).any)

    def test_only_bot_dispatched_runs_are_reserved_successors(self):
        # 自动接力和看门狗用 GITHUB_TOKEN 触发：workflow_dispatch + github-actions[bot]。
        self.assertTrue(publish_loop.is_standby_run(self.relay_item(101)))
        # 有人手动 Run workflow、代码推送：要让位。
        self.assertFalse(publish_loop.is_standby_run(
            self.run_item(101, "queued", "workflow_dispatch", self.HUMAN)))
        self.assertFalse(publish_loop.is_standby_run(self.run_item(101, "queued", "push", self.BOT)))
        # 字段缺失时按「要让位」处理：宁可多让一次，也不能让新代码一直排队。
        self.assertFalse(publish_loop.is_standby_run({"id": 101, "event": "workflow_dispatch"}))
        self.assertFalse(publish_loop.is_standby_run({"id": 101, "actor": self.BOT}))
        # 重新运行由人发起时，triggering_actor 是人。
        rerun = self.relay_item(101)
        rerun["triggering_actor"] = self.HUMAN
        self.assertFalse(publish_loop.is_standby_run(rerun))
        self.api_responses(self.runs(self.relay_item(101)))
        found = publish_loop.find_successors(self.config())
        self.assertEqual((found.takeover, found.standby), (False, True))

    def test_successor_query_targets_this_workflow_and_branch(self):
        self.api_responses(self.runs())
        publish_loop.find_successors(self.config(branch="arena/x-1", workflow="aibrief.yml"))
        self.assertEqual(self.gh_calls(), [[
            "api",
            "repos/owner/repo/actions/workflows/aibrief.yml/runs?branch=arena%2Fx-1&per_page=10"]])

    def test_without_a_run_id_the_api_is_not_queried(self):
        self.steps({"content": "A"})
        self.run_loop(loop_seconds=0.0, publish=False, relay=False, run_id=0,
                      out_dir=self.tmp / "preview")
        self.assertEqual(self.gh_calls(), [])

    def test_relays_once_after_a_full_loop(self):
        self.steps({"content": "A"})
        stats = self.run_loop(loop_seconds=300.0)
        self.assertTrue(stats.relayed)
        self.assertEqual(self.dispatch_calls(), [[
            "workflow", "run", "aibrief.yml", "--repo", "owner/repo", "--ref", "main",
            "-f", "relay=true"]])
        self.assertEqual(publish_loop.exit_code(stats), 0)

    def test_relay_is_skipped_when_a_successor_is_already_queued(self):
        self.steps({"content": "A"})
        # 5 个周期里都没有后继；收尾前再查一次时已经有人在排队。
        self.api_responses(*([self.runs()] * 5), self.runs((101, "queued")))
        stats = self.run_loop(loop_seconds=300.0)
        self.assertFalse(stats.yielded)
        self.assertFalse(stats.relayed)
        self.assertTrue(stats.successor_queued)
        self.assertEqual(self.dispatch_calls(), [])

    # ---- 预约后继：循环中途意外中断时由排队的运行自动续上
    def test_successor_is_reserved_once_the_loop_has_run_for_five_minutes(self):
        self.assertEqual(publish_loop.STANDBY_AFTER_SECONDS, 300)
        self.steps({"content": "A"})
        stats = self.run_loop(loop_seconds=600.0)
        self.assertEqual(self.render_count(), 10)
        # 第 6 个周期（开始后 300 秒）预约，之后每个周期都能查到它在排队，不再重复触发。
        calls = self.gh_calls()
        dispatch_index = next(i for i, call in enumerate(calls) if call[:2] == ["workflow", "run"])
        self.assertEqual(sum(1 for call in calls[:dispatch_index] if call[:1] == ["api"]), 6)
        self.assertEqual(self.dispatch_calls(), [[
            "workflow", "run", "aibrief.yml", "--repo", "owner/repo", "--ref", "main",
            "-f", "relay=true"]])
        # 预约的接力不是「要让位」的后继：这一轮照常跑满，收尾时不用再触发。
        self.assertFalse(stats.yielded)
        self.assertEqual(stats.standby_dispatches, 1)
        self.assertTrue(stats.successor_queued)
        self.assertFalse(stats.relayed)
        self.assertEqual(publish_loop.exit_code(stats), 0)
        self.assertIn("后继已在排队", publish_loop.summary_text(stats))

    def test_no_reservation_before_the_first_successful_render(self):
        # 一直拿不到行情：不预约（避免故障时快速自触发），但跑满后仍然接力。
        self.steps({"exit": 3})
        stats = self.run_loop(loop_seconds=600.0)
        self.assertEqual(stats.standby_dispatches, 0)
        self.assertTrue(stats.relayed)
        self.assertEqual(len(self.dispatch_calls()), 1)
        self.assertEqual(self.api_call_count(), 11)     # 10 个周期 + 收尾

    def test_reservation_is_repeated_when_the_successor_disappears(self):
        self.assertEqual(publish_loop.STANDBY_RETRY_SECONDS, 900)
        self.steps({"content": "A"})
        self.api_responses(self.runs())                  # 预约的运行始终没出现在列表里
        stats = self.run_loop(loop_seconds=1800.0)
        # 第 300 秒预约，15 分钟后（第 1200 秒）再预约；收尾时仍然没有，补触发一次。
        self.assertEqual(stats.standby_dispatches, 2)
        self.assertTrue(stats.relayed)
        self.assertEqual(len(self.dispatch_calls()), 3)

    def test_failed_reservation_is_retried_next_cycle(self):
        self.steps({"content": "A"})
        self.dispatch_codes(1, 0)
        stats = self.run_loop(loop_seconds=600.0)
        self.assertEqual(self.render_count(), 10)
        self.assertEqual(len(self.dispatch_calls()), 2)
        self.assertEqual(stats.standby_dispatches, 1)
        self.assertTrue(stats.successor_queued)
        self.assertEqual(publish_loop.exit_code(stats), 0)
        self.assertIn("预约下一轮失败", self.output.getvalue())

    def test_reservation_needs_a_readable_run_list(self):
        # 查不到运行列表时不知道有没有后继：不盲目预约，收尾时补触发。
        self.steps({"content": "A"})
        self.api_responses({"exit": 1})
        stats = self.run_loop(loop_seconds=600.0)
        self.assertEqual(stats.standby_dispatches, 0)
        self.assertTrue(stats.relayed)
        self.assertEqual(len(self.dispatch_calls()), 1)

    def test_no_reservation_without_relay(self):
        self.steps({"content": "A"})
        stats = self.run_loop(loop_seconds=600.0, relay=False)
        self.assertEqual(self.dispatch_calls(), [])
        self.assertEqual(stats.standby_dispatches, 0)

    def test_yields_to_a_code_push_even_when_a_successor_is_reserved(self):
        self.steps({"content": "A"})
        self.api_responses(self.runs(self.relay_item(101)),
                           self.runs((102, "pending"), self.relay_item(101, "completed")))
        stats = self.run_loop(loop_seconds=600.0)
        self.assertTrue(stats.yielded)
        self.assertEqual(self.render_count(), 2)
        self.assertEqual(self.dispatch_calls(), [])

    def test_yields_to_a_manual_run(self):
        self.steps({"content": "A"})
        self.api_responses(self.runs(self.run_item(101, "queued", "workflow_dispatch", self.HUMAN)))
        stats = self.run_loop(loop_seconds=600.0)
        self.assertTrue(stats.yielded)
        self.assertEqual(self.render_count(), 1)

    def test_short_loops_never_relay(self):
        self.assertGreaterEqual(publish_loop.MIN_RELAY_LOOP_SECONDS, 300)
        self.steps({"content": "A"})
        stats = self.run_loop(loop_seconds=publish_loop.MIN_RELAY_LOOP_SECONDS - 60.0)
        self.assertFalse(stats.relayed)
        self.assertEqual(self.dispatch_calls(), [])

    def test_no_relay_unless_requested(self):
        self.steps({"content": "A"})
        stats = self.run_loop(loop_seconds=300.0, relay=False)
        self.assertFalse(stats.relayed)
        self.assertEqual(self.dispatch_calls(), [])

    def test_failed_dispatch_is_retried(self):
        self.steps({"content": "A"})
        self.dispatch_codes(1, 1, 0)
        stats = self.run_loop(loop_seconds=300.0)
        self.assertTrue(stats.relayed)
        self.assertEqual(len(self.dispatch_calls()), 3)

    def test_dispatch_that_keeps_failing_fails_the_run(self):
        self.steps({"content": "A"})
        self.dispatch_codes(1)
        stats = self.run_loop(loop_seconds=300.0)
        self.assertTrue(stats.relay_failed)
        self.assertEqual(len(self.dispatch_calls()), publish_loop.DISPATCH_ATTEMPTS)
        self.assertEqual(publish_loop.exit_code(stats), 1)

    def test_api_errors_do_not_stop_the_loop(self):
        self.steps({"content": "A"})
        self.api_responses({"exit": 1})
        stats = self.run_loop(loop_seconds=300.0)
        self.assertEqual(self.render_count(), 5)
        self.assertFalse(stats.yielded)
        self.assertTrue(stats.relayed)
        self.api_responses({"raw": "<html>not json</html>"})
        self.assertIsNone(publish_loop.find_successors(self.config()))


class FailureTest(LoopTestCase):
    def test_not_enough_rows_is_skipped_without_failing(self):
        self.steps({"exit": 3, "message": "有效行情不足（6 < 9），放弃更新并保留上一版"},
                   {"content": "A"}, {"exit": 3})
        stats = self.run_loop(loop_seconds=180.0, relay=False)
        self.assertEqual((stats.skipped, stats.pushed, stats.render_errors), (2, 1, 0))
        self.assertEqual(publish_loop.exit_code(stats), 0)
        self.assertEqual(self.origin_log(), [publish_loop.COMMIT_MESSAGE, "initial"])
        self.assertIn("有效行情不足（6 < 9）", self.output.getvalue())

    def test_persistent_render_errors_fail_the_run_but_the_chain_continues(self):
        self.steps({"exit": 5, "message": "排版预检未通过：文字重叠"})
        stats = self.run_loop(loop_seconds=300.0)
        self.assertEqual(self.render_count(), 5)          # 出错也继续每分钟重试
        self.assertEqual(stats.render_errors, 5)
        self.assertTrue(stats.relayed)                    # 下一轮照常接力
        self.assertEqual(publish_loop.exit_code(stats), 1)
        self.assertEqual(self.origin_log(), ["initial"])
        self.assertIn("渲染器退出码 5：排版预检未通过：文字重叠", self.output.getvalue())

    def test_recovered_render_errors_do_not_fail_the_run(self):
        self.steps({"exit": 5}, {"exit": 1}, {"content": "A"})
        stats = self.run_loop(loop_seconds=300.0, relay=False)
        self.assertEqual((stats.render_errors, stats.tail_render_errors), (2, 0))
        self.assertEqual(publish_loop.exit_code(stats), 0)

    def test_a_whole_run_without_data_fails(self):
        self.steps({"exit": 3})
        stats = self.run_loop(loop_seconds=300.0, relay=False)
        self.assertEqual((stats.rendered, stats.skipped), (0, 5))
        self.assertEqual(publish_loop.exit_code(stats), 1)

    def test_a_single_preview_render_without_data_only_warns(self):
        self.steps({"exit": 3})
        stats = self.run_loop(loop_seconds=0.0, publish=False, relay=False,
                              out_dir=self.tmp / "preview")
        self.assertEqual(publish_loop.exit_code(stats), 0)

    def test_a_single_preview_render_that_crashes_fails(self):
        self.steps({"exit": 5})
        stats = self.run_loop(loop_seconds=0.0, publish=False, relay=False,
                              out_dir=self.tmp / "preview")
        self.assertEqual(publish_loop.exit_code(stats), 1)

    def test_missing_renderer_is_an_error_not_a_crash(self):
        stats = self.run_loop(loop_seconds=0.0, publish=False, relay=False,
                              render_cmd=[str(self.tmp / "no-such-renderer")],
                              out_dir=self.tmp / "preview")
        self.assertEqual(stats.render_errors, 1)
        self.assertEqual(publish_loop.exit_code(stats), 1)

    def test_an_unexpected_error_does_not_stop_the_loop(self):
        self.steps({"content": "A"}, {"content": "B"}, {"content": "C"})
        real = publish_loop.render_once
        calls = []

        def flaky(config):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("boom")
            return real(config)

        with mock.patch.object(publish_loop, "render_once", side_effect=flaky):
            stats = self.run_loop(loop_seconds=180.0, relay=False)
        self.assertEqual(stats.iterations, 3)
        self.assertEqual((stats.unexpected_errors, stats.pushed), (1, 2))
        self.assertEqual(publish_loop.exit_code(stats), 0)
        self.assertIn("RuntimeError: boom", self.output.getvalue())
        self.assertIn("意外错误 1", publish_loop.summary_text(stats))

    def test_an_error_between_commit_and_push_leaves_no_local_commit(self):
        # 提交之后、推送之前出了意外：本地多出的提交必须丢掉，否则下一周期会认为
        # 「无变化」，这一版就永远发不出去。
        self.steps({"content": "A"})
        real_git = publish_loop.Publisher.git
        pushes = []

        def git_with_one_crash(publisher, *args):
            if args[:1] == ("push",):
                pushes.append(args)
                if len(pushes) == 1:
                    raise OSError("disk full")
            return real_git(publisher, *args)

        with mock.patch.object(publish_loop.Publisher, "git", git_with_one_crash):
            stats = self.run_loop(loop_seconds=120.0, relay=False)
        self.assertEqual((stats.unexpected_errors, stats.pushed), (1, 1))
        self.assertEqual(self.origin_log(), [publish_loop.COMMIT_MESSAGE, "initial"])
        self.assertEqual(self.origin_file("aibrief/latest.bin"), "latest.bin:A")
        self.assertEqual(git(self.work, "rev-parse", "HEAD"),
                         git(self.origin, "rev-parse", "main"))

    def test_persistent_unexpected_errors_fail_the_run(self):
        # 先成功一次，之后每个周期都出意外：问题仍在持续，这一轮标成失败。
        self.steps({"content": "A"})
        real = publish_loop.render_once
        calls = []

        def broken_after_the_first(config):
            calls.append(1)
            if len(calls) > 1:
                raise RuntimeError("boom")
            return real(config)

        with mock.patch.object(publish_loop, "render_once", side_effect=broken_after_the_first):
            stats = self.run_loop(loop_seconds=360.0, relay=False)
        self.assertEqual((stats.iterations, stats.rendered, stats.unexpected_errors), (6, 1, 5))
        self.assertEqual(publish_loop.exit_code(stats), 1)

    def test_annotations_are_capped(self):
        self.steps({"exit": 5})
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            self.run_loop(loop_seconds=20 * 60.0, relay=False)
        annotations = [line for line in self.output.getvalue().splitlines()
                       if line.startswith("::")]
        self.assertEqual(len(annotations), publish_loop.MAX_ANNOTATIONS)


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "需要 bash 和 jq")
class WatchdogTest(LoopTestCase):
    """看门狗工作流里的脚本：没有未结束的运行时才把循环拉起来。"""

    def script(self) -> str:
        lines = (WORKFLOWS / "aibrief-watchdog.yml").read_text(encoding="utf-8").splitlines()
        start = next(i for i, line in enumerate(lines) if line.strip() == "run: |")
        indent = len(lines[start]) - len(lines[start].lstrip()) + 2
        body = []
        for line in lines[start + 1:]:
            if line.strip() and len(line) - len(line.lstrip()) < indent:
                break
            body.append(line[indent:])
        return "\n".join(body) + "\n"

    def run_watchdog(self) -> subprocess.CompletedProcess:
        env = dict(os.environ, GITHUB_REPOSITORY="owner/repo", BRANCH="main")
        return subprocess.run(["bash", "-c", self.script()], env=env, capture_output=True,
                              text=True, cwd=str(self.tmp))

    def runs(self, *statuses: str) -> dict:
        return {"runs": [{"id": 200 - index, "status": status}
                         for index, status in enumerate(statuses)]}

    def test_script_is_the_whole_step(self):
        script = self.script()
        self.assertTrue(script.startswith("set -euo pipefail\n"))
        self.assertIn("gh workflow run aibrief.yml", script)

    def test_restarts_the_loop_when_nothing_is_running_or_queued(self):
        self.api_responses(self.runs("completed", "completed"))
        done = self.run_watchdog()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.gh_calls()[0][:2], [
            "api", "repos/owner/repo/actions/workflows/aibrief.yml/runs?branch=main&per_page=20"])
        self.assertEqual(self.dispatch_calls(), [[
            "workflow", "run", "aibrief.yml", "--repo", "owner/repo", "--ref", "main",
            "-f", "relay=true"]])

    def test_restarts_the_loop_when_there_are_no_runs_at_all(self):
        self.api_responses(self.runs())
        self.assertEqual(self.run_watchdog().returncode, 0)
        self.assertEqual(len(self.dispatch_calls()), 1)

    def test_leaves_a_running_or_queued_loop_alone(self):
        for status in ("in_progress", "queued", "pending", "waiting", "requested"):
            self.api_responses(self.runs("completed", status, "completed"))
            (self.state / "gh_api.json.count").unlink(missing_ok=True)
            done = self.run_watchdog()
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(self.dispatch_calls(), [], status)

    def test_does_not_restart_blindly_when_the_run_list_cannot_be_read(self):
        self.api_responses({"exit": 1})
        done = self.run_watchdog()
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(self.dispatch_calls(), [])

    def test_failed_restart_fails_the_watchdog_run(self):
        self.api_responses(self.runs("completed"))
        self.dispatch_codes(1)
        self.assertNotEqual(self.run_watchdog().returncode, 0)


class CommandLineTest(unittest.TestCase):
    def parse(self, *argv: str, env: dict | None = None):
        values = {"GITHUB_ACTIONS": "", "GITHUB_REF_NAME": "", "GITHUB_REPOSITORY": "",
                  "GITHUB_RUN_ID": ""}
        values.update(env or {})
        with mock.patch.dict(os.environ, values):
            return publish_loop.parse_args(list(argv))

    def rejects(self, *argv: str, env: dict | None = None) -> str:
        with redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
            self.parse(*argv, env=env)
        self.assertEqual(caught.exception.code, 2)
        return err.getvalue()

    def test_defaults_render_without_publishing(self):
        config = self.parse()
        self.assertFalse(config.publish)
        self.assertFalse(config.relay)
        self.assertEqual(config.out_dir, ROOT / "aibrief")
        self.assertEqual(config.interval, 60.0)
        self.assertEqual(config.loop_seconds, 330 * 60)
        self.assertEqual(config.min_rows, 9)
        self.assertEqual(config.run_id, 0)
        self.assertTrue(config.preview)
        self.assertFalse(self.parse("--no-preview").preview)
        self.assertEqual(config.render_cmd, [sys.executable, str(ROOT / "device_brief" / "render_brief.py")])

    def test_publish_takes_its_context_from_the_actions_environment(self):
        config = self.parse("--publish", env={
            "GITHUB_ACTIONS": "true", "GITHUB_REF_NAME": "main",
            "GITHUB_REPOSITORY": "k-macao/02-rlcd", "GITHUB_RUN_ID": "43714097180"})
        self.assertTrue(config.publish)
        self.assertTrue(config.relay)
        self.assertEqual((config.branch, config.repository, config.run_id),
                         ("main", "k-macao/02-rlcd", 43714097180))
        self.assertFalse(self.parse("--publish", "--no-relay", env={
            "GITHUB_ACTIONS": "true", "GITHUB_REF_NAME": "main"}).relay)

    def test_publish_is_refused_outside_actions(self):
        # --publish 会把工作区重置到远端最新提交，不能在开发机上误用。
        self.assertIn("GitHub Actions", self.rejects("--publish", env={"GITHUB_REF_NAME": "main"}))

    def test_publish_needs_a_branch_and_an_output_directory_inside_the_repository(self):
        self.rejects("--publish", env={"GITHUB_ACTIONS": "true"})
        self.rejects("--publish", "--out-dir", tempfile.gettempdir(),
                     env={"GITHUB_ACTIONS": "true", "GITHUB_REF_NAME": "main"})

    def test_limits(self):
        self.rejects("--interval", "5")
        self.rejects("--loop-seconds", str(6 * 60 * 60))
        self.rejects("--loop-seconds", "-1")
        self.rejects("--min-rows", "0")
        self.rejects("--min-rows", "13")
        self.assertEqual(self.parse("--interval", "30", "--loop-seconds", "0").loop_seconds, 0)

    def test_main_reports_the_loop_result(self):
        stats = publish_loop.Stats(iterations=5, rendered=5, pushed=2, unchanged=3, relayed=True)
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            # 测试本身也在 Actions 里跑：这里先按「不在 Actions 里」的环境来。
            with mock.patch.object(publish_loop, "run_loop", return_value=stats), \
                    mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary),
                                                 "GITHUB_ACTIONS": ""}), \
                    redirect_stdout(io.StringIO()) as out:
                code = publish_loop.main(["--loop-seconds", "0"])
            self.assertEqual(code, 0)
            self.assertIn("共 5 个周期：发布 2，无变化 3，发布失败 0", out.getvalue())
            self.assertIn("已触发下一轮", summary.read_text(encoding="utf-8"))
            self.assertNotIn("::notice", out.getvalue())
        # 在 Actions 里每轮留一条 notice：不用翻日志就能看到这一轮的结果。
        with mock.patch.object(publish_loop, "run_loop", return_value=stats), \
                mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": ""}), \
                redirect_stdout(io.StringIO()) as out:
            publish_loop.main(["--loop-seconds", "0"])
        notices = [line for line in out.getvalue().splitlines() if line.startswith("::notice")]
        self.assertEqual(len(notices), 1)
        self.assertIn("发布 2，无变化 3", notices[0])
        # 只渲染不发布（分支上的单次预览）时不写「发布 0」这种容易误会的话。
        self.assertIn("共 1 个周期：渲染成功 1（不发布），",
                      publish_loop.summary_text(publish_loop.Stats(iterations=1, rendered=1)))
        failed = publish_loop.Stats(iterations=5, rendered=0, skipped=5)
        with mock.patch.object(publish_loop, "run_loop", return_value=failed), \
                mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": "", "GITHUB_ACTIONS": ""}), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(publish_loop.main(["--loop-seconds", "0"]), 1)


class WorkflowTest(unittest.TestCase):
    """工作流文件与脚本必须对得上：参数、时长、权限、触发方式。"""

    def setUp(self):
        self.loop = (WORKFLOWS / "aibrief.yml").read_text(encoding="utf-8")
        self.watchdog = (WORKFLOWS / "aibrief-watchdog.yml").read_text(encoding="utf-8")

    def setting(self, name: str) -> int:
        return int(re.search(rf'^\s+{name}: "(\d+)"', self.loop, re.M).group(1))

    def test_loop_updates_once_a_minute(self):
        self.assertEqual(self.setting("INTERVAL_SECONDS"), 60)
        self.assertIn('--interval "$INTERVAL_SECONDS"', self.loop)

    def test_loop_fits_inside_one_job(self):
        loop_minutes = self.setting("LOOP_MINUTES")
        self.assertLessEqual(loop_minutes * 60, publish_loop.MAX_LOOP_SECONDS)
        self.assertGreaterEqual(loop_minutes * 60, publish_loop.MIN_RELAY_LOOP_SECONDS)
        job = self.loop[self.loop.index("  render:"):]
        timeout = int(re.search(r"timeout-minutes: (\d+)", job).group(1))
        # 留出环境准备与收尾的余量，且不超过 GitHub 的 6 小时上限。
        self.assertGreaterEqual(timeout, loop_minutes + 15)
        self.assertLess(timeout, 360)

    def test_loop_is_not_driven_by_a_schedule(self):
        # 定时器在看门狗里；循环自己接力，否则每次定时触发都会打断正在跑的循环。
        self.assertNotIn("schedule:", self.loop)
        self.assertNotIn("cron:", self.loop)
        self.assertEqual(self.watchdog.count("- cron:"), 1)

    def test_only_the_default_branch_publishes_and_relays(self):
        self.assertIn("github.ref_name == github.event.repository.default_branch", self.loop)
        publish_branch = self.loop[self.loop.index('if [[ "$PUBLISH" == "true" ]]; then'):]
        publish_branch, preview_branch = publish_branch.split("else", 1)
        self.assertIn("--publish", publish_branch)
        self.assertNotIn("--publish", preview_branch)
        self.assertIn("--loop-seconds 0", preview_branch)

    def loop_commands(self) -> list[str]:
        """工作流里对 publish_loop.py 的调用（含反斜杠续行）。"""
        lines = self.loop.splitlines()
        commands = []
        for index, line in enumerate(lines):
            if "python3 device_brief/publish_loop.py" not in line:
                continue
            cursor = index
            while lines[cursor].rstrip().endswith("\\"):
                cursor += 1
            commands.append("\n".join(lines[index:cursor + 1]))
        return commands

    def test_every_option_in_the_workflow_exists(self):
        with redirect_stdout(io.StringIO()) as usage, self.assertRaises(SystemExit):
            publish_loop.parse_args(["--help"])
        # 两处调用：默认分支上的发布循环、其他分支上的单次预览。
        options = [re.findall(r"(?<![\w-])--[a-z][a-z-]+", command)
                   for command in self.loop_commands()]
        self.assertEqual(options, [
            ["--out-dir", "--publish", "--no-preview", "--interval", "--loop-seconds",
             "--min-rows"],
            ["--out-dir", "--loop-seconds", "--min-rows"],
        ])
        for option in sum(options, []):
            self.assertIn(option, usage.getvalue(), option)

    def test_only_the_per_minute_loop_drops_the_png_preview(self):
        # 每分钟一个提交时 PNG 占提交体积的一半以上；分支上的单次预览要留着它给人看。
        publish, preview = self.loop_commands()
        self.assertIn("--no-preview", publish)
        self.assertNotIn("--no-preview", preview)

    def test_relay_input_and_permissions(self):
        # 接力和看门狗都用 `-f relay=true` 触发本工作流，并需要 actions: write。
        self.assertRegex(self.loop, r"relay:\n(?:\s+.*\n)*?\s+type: boolean")
        self.assertIn("inputs.relay", self.loop)
        for text in (self.loop, self.watchdog):
            self.assertIn("actions: write", text)
        self.assertIn("contents: write", self.loop)
        self.assertIn("GH_TOKEN: ${{ github.token }}", self.loop)
        self.assertIn("GH_TOKEN: ${{ github.token }}", self.watchdog)
        self.assertEqual(publish_loop.DEFAULT_WORKFLOW, "aibrief.yml")
        self.assertIn(f"gh workflow run {publish_loop.DEFAULT_WORKFLOW}", self.watchdog)
        self.assertIn("-f relay=true", self.watchdog)

    def test_only_one_loop_runs_per_branch(self):
        job = self.loop[self.loop.index("  render:"):]
        self.assertIn("group: aibrief-device-image-${{ github.ref }}", job)
        self.assertIn("cancel-in-progress: false", job)

    def test_watchdog_looks_at_unfinished_runs_of_the_loop_workflow(self):
        self.assertIn("actions/workflows/aibrief.yml/runs", self.watchdog)
        self.assertIn('select(.status != "completed")', self.watchdog)

    def test_no_probe_configuration_is_left_behind(self):
        # 在工作分支上实测发布循环时会临时改工作流（让分支也发布、缩短一轮、强杀循环）；
        # 这些改动不能带进 main：分支上也发布会和 main 抢 aibrief/，强杀会让每一轮都失败。
        for name, text in (("aibrief.yml", self.loop), ("aibrief-watchdog.yml", self.watchdog)):
            for marker in ("PROBE", "_probe", "pkill"):
                self.assertNotIn(marker, text, f"{name}: {marker}")
        self.assertEqual(self.setting("LOOP_MINUTES"), 330)
        self.assertFalse((ROOT / "_probe").exists())

    def test_tests_and_loop_files_trigger_the_workflow(self):
        for path in ("device_brief/publish_loop.py", "device_brief/test_publish_loop.py",
                     "device_brief/render_brief.py", ".github/workflows/aibrief.yml",
                     ".github/workflows/aibrief-watchdog.yml"):
            self.assertIn(f'- "{path}"', self.loop, path)
        for script in ("device_brief/test_render_brief.py", "device_brief/test_publish_loop.py"):
            self.assertIn(f"python3 {script}", self.loop)


if __name__ == "__main__":
    unittest.main()
