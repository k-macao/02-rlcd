#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 002「章鱼 AI·全景分析」渲染成 RLCD 设备可显示的多页 400×300 单色图。

杂志网格 2.0：
- 数据源为 002（AH 政策、情绪、热力、趋势分析）
- 字体统一使用阿里巴巴普惠体 3.0（Alibaba PuHuiTi 3.0）
- 400×300 固定网格、全页 16px、标题/数字 Bold、真实页码、双栏快讯与末栏均衡
- FreeType 原生 MONO + 自动 hinting + 整数像素定位，不使用灰度阈值或抖动
- 保存前逐页检查边界、字号、纯黑白像素和叠字；超额时调整快讯版位，不截句、不直接裁页
- 快讯默认「全量推送」：抓到的标题全部排版，255 页装不下才按每源条数逐级收敛
- 单文件包含多页：AIB1 + 宽高 + N*15000 字节位图，固件支持最多 255 页

产物（默认写入 aibrief/）：
  latest.bin  8 字节头 + N*15000 字节 1bpp 位图（MSB first，1=黑），N=页数
  latest.png  第一页 PNG 预览
  page_*.png  每页 PNG 预览
  latest_contact_sheet.png  5 列全页校样墙
  meta.json   生成时间、真实页数、收录口径（全量/精选）、预检结果、SHA-256
  version.txt 设备条件请求用的小清单（sha256 / 页数 / 字节数），内容不变就不下载

用法：
  python3 device_brief/render_brief.py                 # 抓取 + 渲染（002 数据）
  python3 device_brief/render_brief.py --demo          # 离线演示数据
  python3 device_brief/render_brief.py --require-live --require-fresh-market
                                  # 实时源不足或行情不新鲜则失败退出（CI 用）
  python3 device_brief/render_brief.py --out-dir aibrief --pages 20
  python3 device_brief/render_brief.py --news-per-source 3   # 只推每源 3 条精选
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import struct
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
# 优先使用 002 仓库，若不存在则回退到 05（兼容旧环境）
for candidate in (ROOT / "002", ROOT / "02", ROOT / "05"):
    if candidate.is_dir():
        sys.path.insert(0, str(candidate))
        break

try:
    import sources  # type: ignore
except ImportError as e:
    print(f"无法导入 sources（需 002/05 仓库）：{e}", file=sys.stderr)
    sys.exit(2)

from PIL import Image, ImageDraw  # noqa: E402
from mono_text import MonoFont, get_mono_font  # noqa: E402

WIDTH = 400
HEIGHT = 300
# 固件 page_count 是 uint8，协议硬上限就是 255 页（3,825,000 字节位图 ≈ 3.65MB）。
MAX_PAGES = 255
MAGIC = b"AIB1"
# 设备先拉这个几十字节的小清单比对新旧 sha256，只有内容变了才下载整包；
# 否则每 5 分钟整包重下会同时浪费流量并把轮播重置回第 1 页。
MANIFEST_NAME = "version.txt"
MANIFEST_VERSION = 1
# 全网快讯默认「全量推送」：抓到的标题全部排版，不按来源截断。
# 只有在固件 48 页上限内放不下时，才沿下面的阶梯逐级收敛，
# 并取「页数不超上限」的最大一档，尽量把内容推满。
NEWS_PER_SOURCE_FULL = 0
NEWS_PER_SOURCE_LADDER = (20, 16, 12, 8, 6, 5, 4, 3, 2, 1)
LAYOUT_VERSION = "2.4-alibaba-puhuiti-16px-native-mono-255p"
CN_TIMEZONE = ZoneInfo("Asia/Shanghai")


def china_now() -> datetime:
    """服务器可能在 UTC 运行；页面与元数据统一使用北京时间。"""
    return datetime.now(CN_TIMEZONE)


# 全页只使用阿里巴巴普惠体 3.0；缺文件时直接失败，不回退其他字体家族。
FONT_DIR = ROOT / "device_brief" / "fonts"
FONT_PATH = Path(os.environ.get(
    "ALIBABA_PUHUITI_FONT", str(FONT_DIR / "AlibabaPuHuiTi-3-55-Regular.ttf")
))
FONT_BOLD_PATH = Path(os.environ.get(
    "ALIBABA_PUHUITI_BOLD_FONT", str(FONT_DIR / "AlibabaPuHuiTi-3-85-Bold.ttf")
))

BLACK, WHITE = 0, 255

# 旧单页接口保留原有 8px 边距；设备多页统一使用下面的杂志网格。
MARGIN = 8
CONTENT_W = WIDTH - MARGIN * 2
# 全页统一 16px；标题与完整数值串使用 Bold，其余文字保持 Regular。
FONT_TITLE = 16
FONT_SECTION = 16
FONT_BODY = 16
FONT_SMALL = 16
FONT_META = 16
FONT_MIN = 16

# 4.2" RLCD / 400×300 杂志网格。正文和页脚分别留出安全区，
# 任何文字都不得依赖裁切、缩字或叠字来“放下”。
PAGE_MARGIN = 12
PAGE_CONTENT_W = WIDTH - PAGE_MARGIN * 2
BODY_TOP = 48
BODY_BOTTOM = 273
BODY_HEIGHT = BODY_BOTTOM - BODY_TOP
FOOTER_RULE_Y = 278
COLUMN_GAP = 14
COLUMN_W = (PAGE_CONTENT_W - COLUMN_GAP) // 2
NEWS_NUMBER_W = 36
LEADING_BODY = 20
LEADING_SMALL = 20
LEADING_META = 20
LEADING_MIN = 20

# 数字及与其组成一个数值串的符号使用 Bold；其余字符使用 Regular。
_NUMERIC_RUN = re.compile(r"[+-]?\d+(?:[.,:/-]\d+)*%?")

def build_policy_deep_layer(brief: dict, policy: dict | None) -> dict | None:
    """组装「AI 政策深度 / AI 政策研报」所需的深度层（知识库 + 舆情 + 传导 + 推理 + 模型）。

    ``sources.analyze_policy_deep()`` 内部只用本地政策知识库与可选时序模型，
    不额外联网；失败时返回 ``None``，设备图只是少这两栏，不影响其它页面。
    """
    if not policy:
        return None
    try:
        series = sources.get_policy_series(None)
        return sources.analyze_policy_deep(policy, brief, series, top_k=3)
    except Exception as exc:  # pragma: no cover - 依赖 002 内部结构
        print(f"政策深度层生成失败：{exc}", file=sys.stderr)
        return None


def collect_brief_002(limit: int = 12, demo: bool = False):
    """采集 002 数据；``demo=True`` 时完全离线，不触碰任何网络接口。"""
    if demo:
        brief = {name: sources._demo_items(name) for name in sources.SOURCES}
        summary = sources.analyze_brief(brief)
        market_ashare = copy.deepcopy(sources._ASHARE_SNAPSHOT)
        market_hk = copy.deepcopy(sources._HK_SNAPSHOT)
        market_us = copy.deepcopy(sources._US_SNAPSHOT)
        return {
            "brief": brief,
            "summary": summary,
            "market_ashare": market_ashare,
            "review_ashare": sources.analyze_ashare(market_ashare),
            "market_hk": market_hk,
            "review_hk": sources.analyze_hk(market_hk),
            "market_us": market_us,
            "review_us": sources.analyze_us(market_us),
            "heatmap": summary.get("heatmap") or sources.analyze_keyword_heatmap(brief, top_n=40),
            "policy": summary.get("policy") or sources.analyze_policy(brief, deep=False),
            "policy_deep": None,
            "policy_deep_layer": build_policy_deep_layer(
                brief, summary.get("policy") or sources.analyze_policy(brief, deep=False)),
            "market_freshness": {
                "ok": False,
                "source": "snapshot",
                "reason": "离线演示数据，不参与实时新鲜度认证",
            },
        }

    brief = sources.collect_all(limit=limit)

    # 基础分析
    summary = sources.analyze_brief(brief)

    # 行情
    market_ashare = None
    review_ashare = None
    market_hk = None
    review_hk = None
    market_us = None
    review_us = None
    market_freshness = {"ok": False, "source": "unknown", "reason": "尚未执行行情新鲜度检查"}
    try:
        # 与 002 推送共用同一道闸门；日 K 只抓一次，同时生成行情和新鲜度结果。
        market_ashare, market_freshness = sources.collect_market_for_push()
        review_ashare = sources.analyze_ashare(market_ashare)
    except Exception as exc:
        market_freshness = {"ok": False, "source": "unknown", "reason": f"A 股行情获取失败：{exc}"}
        print(f"A 股行情获取失败：{exc}", file=sys.stderr)
    try:
        market_hk = sources.get_hk_market()
        review_hk = sources.analyze_hk(market_hk)
    except Exception as exc:
        print(f"港股行情获取失败：{exc}", file=sys.stderr)
    try:
        market_us = sources.get_us_market()
        review_us = sources.analyze_us(market_us)
    except Exception as exc:
        print(f"美股行情获取失败：{exc}", file=sys.stderr)

    # 情绪热力图
    heatmap = None
    try:
        heatmap = sources.analyze_keyword_heatmap(brief, top_n=40)
    except Exception as exc:
        print(f"热力图分析失败：{exc}", file=sys.stderr)

    # 政策分析
    policy = None
    policy_deep = None
    try:
        policy = sources.analyze_policy(brief, deep=False)
        # deep 分析需要更多计算，可能较慢，尝试获取
        policy_deep = sources.analyze_policy(brief, deep=True)
    except Exception as exc:
        print(f"政策分析失败：{exc}", file=sys.stderr)
    policy_deep_layer = build_policy_deep_layer(brief, policy)

    return {
        "brief": brief,
        "summary": summary,
        "market_ashare": market_ashare,
        "review_ashare": review_ashare,
        "market_hk": market_hk,
        "review_hk": review_hk,
        "market_us": market_us,
        "review_us": review_us,
        "heatmap": heatmap,
        "policy": policy,
        "policy_deep": policy_deep,
        "policy_deep_layer": policy_deep_layer,
        "market_freshness": market_freshness,
    }

def live_source_stats(brief: dict) -> tuple[int, int]:
    live = 0
    total = len(brief)
    # 002 的 _DEMO 结构与 05 类似，尝试兼容
    demo_dict = getattr(sources, "_DEMO", {})
    for name, items in brief.items():
        demo_titles = {t.strip().lower() for t in demo_dict.get(name, [])}
        titles = [str(i.get("title", "")).strip().lower() for i in items]
        if any(t and t not in demo_titles for t in titles):
            live += 1
    return live, total

def pick_headlines(brief: dict, limit: int = 4) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    demo_dict = getattr(sources, "_DEMO", {})
    for name, items in brief.items():
        demo_titles = {t.strip().lower() for t in demo_dict.get(name, [])}
        real = [i for i in items if str(i.get("title", "")).strip().lower() not in demo_titles]
        for item in (real or items):
            title = " ".join(str(item.get("title", "")).split())
            key = title.lower()
            if not title or key in seen:
                continue
            seen.add(key)
            out.append(title)
            if len(out) >= limit:
                return out
    return out

def headline_candidates(brief: dict, limit: int = 6) -> list[str]:
    return pick_headlines(brief, limit=limit)

# ---------------------------------------------------------------- 排版工具
class Sheet:
    def __init__(self, font_path: Path | None = None,
                 bold_font_path: Path | None = None):
        self.image = Image.new("L", (WIDTH, HEIGHT), WHITE)
        self.draw = ImageDraw.Draw(self.image)
        self.font_path = font_path or FONT_PATH
        self.bold_font_path = bold_font_path or FONT_BOLD_PATH
        self._cache: dict[tuple[str, int, str], MonoFont] = {}
        # 每次文字绘制都留档，保存前可逐页检查字号、边界与叠字。
        self.text_runs: List[dict] = []
        self.region = "body"
        self.editorial = False
        self.page_title = ""
        self.page_subtitle = ""

    def _font(self, is_digital: bool, size: int, bold: bool = False) -> MonoFont:
        # 标题或数值串都使用同一字体家族的 Bold，其余文字使用 Regular。
        use_bold = bool(bold or is_digital)
        weight = "Bold" if use_bold else "Regular"
        font_path = self.bold_font_path if use_bold else self.font_path
        key = (str(font_path), size, weight)
        if key not in self._cache:
            self._cache[key] = get_mono_font(font_path, size)
        return self._cache[key]

    def _is_digital_char(self, ch: str) -> bool:
        return ch.isascii() and ch.isdigit()

    def _runs(self, text: str):
        """按完整数值串分段，避免小数、日期和百分比丢失字距或符号。"""
        offset = 0
        for match in _NUMERIC_RUN.finditer(text):
            if match.start() > offset:
                yield False, text[offset:match.start()]
            yield True, match.group(0)
            offset = match.end()
        if offset < len(text):
            yield False, text[offset:]

    def font(self, size: int, bold: bool = False) -> MonoFont:
        return self._font(False, size, bold)

    def measure(self, text: str, size: int, bold: bool = False) -> int:
        return sum(
            self._font(is_digital, size, bold).measure(segment)
            for is_digital, segment in self._runs(str(text))
        )

    def _wrap_units(self, text: str, size: int, max_width: int,
                    bold: bool = False) -> list[str]:
        """数值串完整保留；单个数值本身超宽时才退回逐字符折行。"""
        units: list[str] = []
        for is_digital, segment in self._runs(text):
            if is_digital and self.measure(segment, size, bold) <= max_width:
                units.append(segment)
            else:
                units.extend(segment)
        return units

    def text(self, x: int, y: int, text: str, size: int = 16, bold: bool = False,
             fill: int = BLACK) -> int:
        text = str(text)
        if not text:
            return 0
        origin_x = int(round(x))
        origin_y = int(round(y))
        cursor = origin_x
        bbox = None
        for is_digital, segment in self._runs(text):
            font = self._font(is_digital, size, bold)
            advance, segment_bbox = font.render(self.draw, cursor, origin_y, segment, fill)
            if segment_bbox is not None:
                if bbox is None:
                    bbox = segment_bbox
                else:
                    bbox = (
                        min(bbox[0], segment_bbox[0]), min(bbox[1], segment_bbox[1]),
                        max(bbox[2], segment_bbox[2]), max(bbox[3], segment_bbox[3]),
                    )
            cursor += advance
        self.text_runs.append({
            "text": text, "size": size, "region": self.region,
            "bold": bool(bold),
            "bbox": bbox or (origin_x, origin_y, origin_x, origin_y),
        })
        return cursor - origin_x

    def wrap(self, text: str, size: int, max_width: int, max_lines: int = 2,
             bold: bool = False) -> list[str]:
        text = " ".join(str(text).split())
        if not text:
            return []
        lines: list[str] = []
        current = ""
        for unit in self._wrap_units(text, size, max_width, bold):
            trial = current + unit
            if self.measure(trial, size, bold) <= max_width:
                current = trial
                continue
            if current:
                lines.append(current)
                if len(lines) == max_lines:
                    break
                current = unit
            else:
                current = unit
        if len(lines) < max_lines and current:
            lines.append(current)
        if len(lines) == max_lines:
            consumed = sum(len(line) for line in lines)
            if consumed < len(text):
                last = lines[-1]
                while last and self.measure(last + "…", size, bold) > max_width:
                    last = last[:-1]
                lines[-1] = last + "…"
        return lines

    def wrap_all(self, text: str, size: int, max_width: int,
                 bold: bool = False) -> list[str]:
        """不限制行数，完整折行，全部内容都显示。"""
        text = " ".join(str(text).split())
        if not text:
            return []
        lines: list[str] = []
        current = ""
        for unit in self._wrap_units(text, size, max_width, bold):
            trial = current + unit
            if self.measure(trial, size, bold) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = unit
        if current:
            lines.append(current)
        return lines

    def rule(self, x1: int, y: int, x2: int, thickness: int = 1, fill: int = BLACK):
        self.draw.rectangle((x1, y, x2, y + thickness - 1), fill=fill)

    def box(self, x1: int, y: int, x2: int, y2: int, fill: int = BLACK):
        self.draw.rectangle((x1, y, x2, y2), fill=fill)

    def inverted(self, x: int, y: int, text: str, size: int = 16, bold: bool = True,
                 pad_x: int = 5, pad_y: int = 2) -> int:
        width = self.measure(text, size, bold)
        height = size + 3
        self.box(x, y, x + width + pad_x * 2 - 1, y + height + pad_y * 2 - 1)
        self.text(x + pad_x, y + pad_y - 1, text, size, bold, WHITE)
        return width + pad_x * 2

    def section(self, y: int, title: str, right: str = "",
                right_size: int = 16) -> int:
        self.inverted(MARGIN, y, title, FONT_SECTION, True)
        if right:
            width = self.measure(right, right_size)
            self.text(WIDTH - MARGIN - width, y + 4, right, right_size, False)
        return y + FONT_SECTION + 10

    def header_bar(self, title: str, subtitle: str = "", page_info: str = ""):
        """顶部黑条，显示标题和页码"""
        self.box(0, 0, WIDTH - 1, 28)
        self.text(MARGIN, 5, title, FONT_SECTION, True, WHITE)
        if subtitle:
            # 右上角小字
            w = self.measure(subtitle, FONT_META)
            self.text(WIDTH - MARGIN - w, 2, subtitle, FONT_META, False, WHITE)
        if page_info:
            w = self.measure(page_info, FONT_MIN)
            self.text(WIDTH - MARGIN - w, 16, page_info, FONT_MIN, False, WHITE)

    def to_bitmap(self) -> bytes:
        mono = self.image.convert("1", dither=Image.NONE)
        bits = mono.load()
        buf = bytearray(WIDTH * HEIGHT // 8)
        idx = 0
        for yy in range(HEIGHT):
            for xx in range(0, WIDTH, 8):
                byte = 0
                for bit in range(8):
                    if bits[xx + bit, yy] == 0:
                        byte |= 0x80 >> bit
                buf[idx] = byte
                idx += 1
        return bytes(buf)

    def save_single(self, out_dir: Path, meta: dict) -> None:
        """兼容旧单页保存（用于测试）"""
        out_dir.mkdir(parents=True, exist_ok=True)
        mono = self.image.convert("1", dither=Image.NONE)
        mono.save(out_dir / "latest.png")
        buf = self.to_bitmap()
        header = MAGIC + struct.pack("<HH", WIDTH, HEIGHT)
        payload = header + buf
        (out_dir / "latest.bin").write_bytes(payload)
        meta = dict(meta)
        meta.update({
            "width": WIDTH,
            "height": HEIGHT,
            "bytes": len(payload),
            "pages": 1,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "crc32_bitmap": f"{zlib.crc32(buf) & 0xffffffff:08x}",
        })
        (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def save(self, out_dir: Path, meta: dict) -> None:
        self.save_single(out_dir, meta)

def manifest_text(sha256: str, pages: int, size: int, generated_at: str = "") -> str:
    """设备条件请求用的极简清单：一行几个 key=value，几十字节即可判断要不要下载整包。"""
    fields = [
        f"manifest={MANIFEST_VERSION}",
        f"sha256={sha256}",
        f"pages={pages}",
        f"bytes={size}",
    ]
    if generated_at:
        fields.append(f"generated_at={generated_at}")
    return " ".join(fields) + "\n"


def parse_manifest(text: str) -> dict:
    """解析清单文本；格式不对时返回空 dict，设备端按「未知」处理并回退整包下载。"""
    out: dict = {}
    for token in str(text or "").replace("\n", " ").split():
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        out[key.strip()] = value.strip()
    return out


def write_manifest(out_dir: Path, meta: dict) -> Path:
    """把 sha256 / 页数 / 字节数写到 ``version.txt``，供设备先比对再决定是否下载。"""
    text = manifest_text(
        str(meta.get("sha256") or ""),
        _int(meta.get("pages")),
        _int(meta.get("bytes")),
        str(meta.get("generated_at") or ""),
    )
    path = Path(out_dir) / MANIFEST_NAME
    path.write_text(text, encoding="utf-8")
    return path


class LayoutError(RuntimeError):
    """任一页未通过预检时阻止写入设备负载。"""


class MultiPageRenderer:
    def __init__(self, font_path: Path | None = None,
                 bold_font_path: Path | None = None):
        self.font_path = font_path or FONT_PATH
        self.bold_font_path = bold_font_path or FONT_BOLD_PATH
        self.pages: List[Sheet] = []
        self.page_limit = MAX_PAGES
        self.layout_meta: Dict[str, Any] = {}
        self._finalized = False
        self._now = china_now()

    def new_page(self, title: str = "", subtitle: str = "", page_info: str = "") -> Sheet:
        # page_info 仅为旧调用签名保留；页码只能在全部分页完成后绘制。
        sheet = Sheet(self.font_path, self.bold_font_path)
        sheet.editorial = True
        sheet.page_title = title or "AI 简报"
        sheet.page_subtitle = subtitle or "OCTOPUS AI / DAILY BRIEF"
        return sheet

    def add_page(self, sheet: Sheet):
        self.pages.append(sheet)
        self._finalized = False

    def finalize(self, now: datetime | None = None):
        """全部内容排版结束后，再统一绘制真实页码与 running header/footer。"""
        if self._finalized:
            return self
        self._now = now or self._now
        if not self.pages:
            sheet = self.new_page("AI 简报", "暂无内容")
            sheet.text(PAGE_MARGIN, BODY_TOP, "暂无简报内容", FONT_BODY)
            self.add_page(sheet)

        total_pages = len(self.pages)
        date_text = self._now.strftime("%Y.%m.%d")
        for index, sheet in enumerate(self.pages, 1):
            sheet.region = "header"
            sheet.box(0, 0, WIDTH - 1, 2)
            title = sheet.page_title
            title_size = FONT_TITLE
            if sheet.measure(title, title_size, True) > PAGE_CONTENT_W:
                title_size = FONT_SECTION
            sheet.text(PAGE_MARGIN, 0, title, title_size, True)
            sheet.text(PAGE_MARGIN, 22, sheet.page_subtitle, FONT_MIN)
            sheet.rule(PAGE_MARGIN, 44, WIDTH - PAGE_MARGIN - 1, 2)

            sheet.region = "footer"
            right = WIDTH - PAGE_MARGIN - 1
            sheet.rule(PAGE_MARGIN, FOOTER_RULE_Y, right, 1)
            progress_x = PAGE_MARGIN + round((right - PAGE_MARGIN) * index / total_pages)
            sheet.rule(PAGE_MARGIN, FOOTER_RULE_Y, progress_x, 2)
            sheet.text(PAGE_MARGIN, 279, f"章鱼 AI · {date_text}", FONT_MIN)
            # 页码已取消：右下角只保留读取时间（网页端同步去掉分页显示）。
            read_text = f"读取 {self._now.strftime('%H:%M')}"
            sheet.text(WIDTH - PAGE_MARGIN - sheet.measure(read_text, FONT_MIN),
                       279, read_text, FONT_MIN)
            sheet.region = "body"
        self._finalized = True
        return self

    @staticmethod
    def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
        return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])

    def validate(self, max_pages: int | None = None):
        """逐页预检画布、字号、安全区和文字碰撞；失败时不产出任何文件。"""
        self.finalize()
        limit = max_pages or self.page_limit or MAX_PAGES
        errors: List[str] = []
        if not 1 <= len(self.pages) <= limit:
            errors.append(f"页数 {len(self.pages)} 超出本次版位上限 {limit}")

        for page_no, sheet in enumerate(self.pages, 1):
            if sheet.image.size != (WIDTH, HEIGHT):
                errors.append(f"第{page_no}页画布为 {sheet.image.size}，应为 {(WIDTH, HEIGHT)}")
            if any(sheet.image.histogram()[1:255]):
                errors.append(f"第{page_no}页存在灰阶像素，必须使用原生 1bpp 点阵")
            runs = [r for r in sheet.text_runs if r["text"].strip()]
            if sheet.editorial and not any(r["region"] == "body" for r in runs):
                errors.append(f"第{page_no}页没有正文")
            for run in runs:
                x0, y0, x1, y1 = run["bbox"]
                if run["size"] != 16:
                    errors.append(f"第{page_no}页字号 {run['size']} 不是 16px")
                if x0 < 0 or y0 < 0 or x1 > WIDTH or y1 > HEIGHT:
                    errors.append(f"第{page_no}页文字越出画布：{run['text'][:24]!r} {run['bbox']}")
                if sheet.editorial and run["region"] == "body":
                    if y0 < BODY_TOP or y1 > BODY_BOTTOM:
                        errors.append(
                            f"第{page_no}页正文侵入页眉/页脚：{run['text'][:24]!r} {run['bbox']}"
                        )
            for i, first in enumerate(runs):
                for second in runs[i + 1:]:
                    if self._overlap(first["bbox"], second["bbox"]):
                        errors.append(
                            f"第{page_no}页文字叠压：{first['text'][:16]!r} / {second['text'][:16]!r}"
                        )
                if len(errors) >= 12:
                    break
            if len(errors) >= 12:
                break
        if errors:
            raise LayoutError("；".join(errors[:12]))
        return self

    def save(self, out_dir: Path, meta: dict):
        # 必须先完成所有页面和全部校验，再创建/覆盖任何交付文件。
        self.validate()
        out_dir.mkdir(parents=True, exist_ok=True)
        mono_pages = [p.image.convert("1", dither=Image.NONE) for p in self.pages]
        bitmaps = b"".join(p.to_bitmap() for p in self.pages)
        payload = MAGIC + struct.pack("<HH", WIDTH, HEIGHT) + bitmaps
        (out_dir / "latest.bin").write_bytes(payload)
        mono_pages[0].save(out_dir / "latest.png")

        expected_previews = set()
        for i, mono in enumerate(mono_pages, 1):
            name = f"page_{i:02d}.png"
            expected_previews.add(name)
            mono.save(out_dir / name)
        # 页数变少时移除旧校样，避免把上一版残页当成新内容推送。
        for old in out_dir.glob("page_*.png"):
            if old.name not in expected_previews:
                old.unlink()

        combined = Image.new("1", (WIDTH, HEIGHT * len(mono_pages)), 1)
        for i, mono in enumerate(mono_pages):
            combined.paste(mono, (0, i * HEIGHT))
        combined.save(out_dir / "latest_combined.png")

        # 5 列校样墙：行数按实际页数计算，可在推送前逐页目检。
        columns = 5
        gap = 8
        rows = math.ceil(len(mono_pages) / columns)
        contact = Image.new(
            "1", (columns * WIDTH + (columns + 1) * gap,
                  rows * HEIGHT + (rows + 1) * gap), 1
        )
        for i, mono in enumerate(mono_pages):
            x = gap + (i % columns) * (WIDTH + gap)
            y = gap + (i // columns) * (HEIGHT + gap)
            contact.paste(mono, (x, y))
        contact.save(out_dir / "latest_contact_sheet.png")

        meta = dict(meta)
        meta.update(self.layout_meta)
        meta.update({
            "width": WIDTH,
            "height": HEIGHT,
            "pages": len(self.pages),
            "bytes": len(payload),
            "page_bytes": len(bitmaps),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "crc32_bitmap": f"{zlib.crc32(bitmaps) & 0xffffffff:08x}",
            "font": str(self.font_path.name),
            "bold_font": str(self.bold_font_path.name),
            "font_family": "Alibaba PuHuiTi 3.0",
            "font_size": 16,
            "numeric_font_weight": "Bold",
            "title_font_weight": "Bold",
            "text_font_weight": "Regular",
            "rasterizer": "FreeType FT_LOAD_TARGET_MONO + FT_LOAD_FORCE_AUTOHINT",
            "pixel_positioning": "integer",
            "gray_levels": 2,
            "layout": LAYOUT_VERSION,
            "layout_validated": True,
            "safe_area": [PAGE_MARGIN, BODY_TOP, WIDTH - PAGE_MARGIN, BODY_BOTTOM],
            "page_titles": [p.page_title for p in self.pages],
        })
        (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        write_manifest(out_dir, meta)
        print(f"已逐页预检并生成 {len(self.pages)} 页，{out_dir}/latest.bin ({len(payload)} bytes)")

# ---------------------------------------------------------------- 兼容旧接口（用于测试）
def fmt_pct(value) -> str:
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "--"

def fmt_amount(value) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "--"
    return f"{amount / 10000:.2f}万亿" if amount >= 10000 else f"{amount:,.0f}亿"

def sector_line(sheet: Sheet, summary: dict, max_width: int) -> str:
    opp = [i for i in (summary.get("opportunity_sectors") or [])[:2] if i.get("tag")]
    press = [i for i in (summary.get("pressure_sectors") or [])[:1] if i.get("tag")]

    def build(with_sources: bool, with_net: bool) -> str:
        def fmt(item: dict) -> str:
            text = str(item["tag"])
            if with_net:
                text += f"{int(item.get('net') or 0):+d}"
            if with_sources:
                text += f"·{int(item.get('sources') or 0)}源"
            return text

        parts = []
        if opp:
            parts.append("利好 " + " ".join(fmt(i) for i in opp))
        if press:
            parts.append("承压 " + " ".join(fmt(i) for i in press))
        return "｜".join(parts)

    if opp or press:
        for with_sources, with_net in ((True, True), (False, True), (False, False)):
            line = build(with_sources, with_net)
            if sheet.measure(line, FONT_BODY) <= max_width:
                return line
        return sheet.wrap(build(False, False), FONT_BODY, max_width, 1)[0]
    up = " / ".join(summary.get("sectors_up") or [])
    down = " / ".join(summary.get("sectors_down") or [])
    parts = []
    if up:
        parts.append(f"利好 {up}")
    if down:
        parts.append(f"承压 {down}")
    if not parts:
        return "暂无明确方向"
    return sheet.wrap("｜".join(parts), FONT_BODY, max_width, 1)[0]

def draw_sheet(summary: dict, market: dict | None, review: dict | None,
               headlines: list[str], now: datetime, live: int, total: int,
               demo: bool) -> Sheet:
    """兼容旧单页绘制（测试用），使用阿里巴巴普惠体"""
    sheet = Sheet(FONT_PATH)
    right_edge = WIDTH - MARGIN

    sheet.box(0, 0, WIDTH - 1, 33)
    sheet.text(MARGIN, 6, "章鱼 AI · 全景分析", FONT_TITLE, True, WHITE)
    date_text = now.strftime("%m月%d日 %H:%M")
    sheet.text(right_edge - sheet.measure(date_text, FONT_META), 3,
               date_text, FONT_META, False, WHITE)
    live_text = "离线演示数据" if demo else f"实时源 {live}/{total}"
    sheet.text(right_edge - sheet.measure(live_text, FONT_MIN), 18,
               live_text, FONT_MIN, False, WHITE)

    bias = str(summary.get("bias") or "中性")
    bull = int(summary.get("bull") or 50)
    bear = 100 - bull
    signals = int(summary.get("signals") or 0)
    sentiment = str(summary.get("headline") or "")

    y = sheet.section(37, "AI 每日总结",
                      f"{bias} 多{bull}% 空{bear}% · {signals}信号")
    for line in sheet.wrap(sentiment or "暂无市场总结", FONT_BODY, CONTENT_W, 2):
        sheet.text(MARGIN, y, line, FONT_BODY)
        y += 18
    sheet.text(MARGIN, y, "板块", FONT_BODY, True)
    body_x = MARGIN + sheet.measure("板块", FONT_BODY, True) + 6
    sheet.text(body_x, y, sector_line(sheet, summary, right_edge - body_x),
               FONT_BODY)
    y += 18

    sheet.rule(MARGIN, y + 1, right_edge - 1, 1)
    review_date = str((review or {}).get("date") or (market or {}).get("date") or "--")
    y = sheet.section(y + 4, "AI 看盘 · A股", f"{review_date}")
    indices = (review or {}).get("indices") or []
    if indices:
        shown = indices[:4]
        for i, item in enumerate(shown):
            x = MARGIN + (i % 2) * 196
            yy = y + (i // 2) * 19
            raw_name = str(item.get("name") or "")
            name = raw_name.replace("上证指数", "上证").replace("深证成指", "深证").replace("创业板指", "创业板")
            close = item.get("close")
            pct = fmt_pct(item.get("pct"))
            close_text = f"{float(close):,.2f}" if isinstance(close, (int, float)) else "--"
            sheet.text(x, yy, name, FONT_BODY, True)
            name_w = sheet.measure(name, FONT_BODY, True)
            sheet.text(x + name_w + 5, yy + 1, close_text, FONT_SMALL)
            sheet.text(x + 184 - sheet.measure(pct, FONT_SMALL, True), yy + 1,
                       pct, FONT_SMALL, True)
        y += ((len(shown) + 1) // 2) * 19 + 2

    sheet.rule(MARGIN, y + 1, right_edge - 1, 1)
    y = sheet.section(y + 4, "全网快讯", "仅供参考", right_size=FONT_META)
    if headlines:
        for title in headlines[:3]:
            sheet.draw.rectangle((MARGIN, y + 5, MARGIN + 3, y + 8), fill=BLACK)
            line = sheet.wrap(title, FONT_SMALL, CONTENT_W - 10, 1, True)
            if line:
                sheet.text(MARGIN + 10, y, line[0], FONT_SMALL, True)
            y += 16
    return sheet

# ---------------------------------------------------------------- 多页杂志排版（400×300）
class PageFlow:
    """在固定安全区内顺序排版；只翻页，不缩字、不截句。"""

    def __init__(self, renderer: MultiPageRenderer, title: str, subtitle: str = ""):
        self.renderer = renderer
        self.title = title
        self.subtitle = subtitle
        self._continued = False
        self.sheet: Sheet
        self.y = BODY_TOP
        self._start_page()

    def _start_page(self):
        subtitle = self.subtitle + (" · 续" if self._continued and self.subtitle else "")
        self.sheet = self.renderer.new_page(self.title, subtitle)
        self.renderer.add_page(self.sheet)
        self.y = BODY_TOP

    def new_page(self):
        self._continued = True
        self._start_page()

    def ensure(self, height: int):
        if height <= BODY_HEIGHT and self.y + height > BODY_BOTTOM:
            self.new_page()
        elif self.y >= BODY_BOTTOM:
            self.new_page()

    def add_text(self, text: str, size: int = FONT_BODY,
                 leading: int = LEADING_BODY, indent: int = 0, gap: int = 6,
                 bold: bool = False):
        lines = self.sheet.wrap_all(text, size, PAGE_CONTENT_W - indent, bold)
        if not lines:
            return
        if len(lines) * leading <= BODY_HEIGHT:
            self.ensure(len(lines) * leading)
        for line in lines:
            if self.y + leading > BODY_BOTTOM:
                self.new_page()
            self.sheet.text(PAGE_MARGIN + indent, self.y, line, size, bold)
            self.y += leading
        self.y += gap

    def add_rich(self, paragraphs: List[dict], gap: int = 7, separator: bool = False):
        """排版一组不同层级的完整段落；一块能放下时避免跨页拆散。"""
        specs: List[tuple[str, int, int, int, bool]] = []
        for paragraph in paragraphs:
            text = str(paragraph.get("text") or "")
            size = int(paragraph.get("size") or FONT_BODY)
            leading = int(paragraph.get("leading") or (size + 6))
            indent = int(paragraph.get("indent") or 0)
            bold = bool(paragraph.get("bold"))
            for line in self.sheet.wrap_all(text, size, PAGE_CONTENT_W - indent, bold):
                specs.append((line, size, leading, indent, bold))
        if not specs:
            return
        height = sum(spec[2] for spec in specs)
        if height <= BODY_HEIGHT:
            self.ensure(height)
        for line, size, leading, indent, bold in specs:
            if self.y + leading > BODY_BOTTOM:
                self.new_page()
            self.sheet.text(PAGE_MARGIN + indent, self.y, line, size, bold)
            self.y += leading
        if separator and self.y + 4 < BODY_BOTTOM:
            self.sheet.rule(PAGE_MARGIN, self.y + 2, WIDTH - PAGE_MARGIN - 1, 1)
            self.y += 5
        self.y += gap

    def add_entry(self, label: str, text: str, size: int = FONT_SMALL, gap: int = 8):
        """带细线的栏目标签，与正文至少两行保持同页。"""
        lines = self.sheet.wrap_all(text, size, PAGE_CONTENT_W)
        if not lines:
            return
        leading = LEADING_SMALL if size == FONT_SMALL else LEADING_BODY
        height = 20 + len(lines) * leading
        if height <= BODY_HEIGHT:
            self.ensure(height)
        else:
            self.ensure(20 + min(2, len(lines)) * leading)
        x = PAGE_MARGIN
        self.sheet.box(x, self.y + 5, x + 5, self.y + 10)
        self.sheet.text(x + 10, self.y, label, FONT_META, True)
        line_start = x + 18 + self.sheet.measure(label, FONT_META, True)
        if line_start < WIDTH - PAGE_MARGIN - 12:
            self.sheet.rule(line_start, self.y + 8, WIDTH - PAGE_MARGIN - 1, 1)
        self.y += 20
        for line in lines:
            if self.y + leading > BODY_BOTTOM:
                self.new_page()
            self.sheet.text(PAGE_MARGIN, self.y, line, size)
            self.y += leading
        self.y += gap

    def add_metrics(self, metrics: List[tuple[str, str]]):
        if not metrics:
            return
        height = 58
        self.ensure(height)
        x0 = PAGE_MARGIN
        right = WIDTH - PAGE_MARGIN
        cell_w = PAGE_CONTENT_W // len(metrics)
        self.sheet.rule(x0, self.y, right - 1, 1)
        for i, (label, value) in enumerate(metrics):
            x = x0 + i * cell_w
            if i:
                self.sheet.draw.line((x, self.y + 7, x, self.y + 49), fill=BLACK)
            self.sheet.text(x + 7, self.y + 7, label, FONT_MIN)
            value_size = FONT_TITLE if self.sheet.measure(value, FONT_TITLE) <= cell_w - 14 else FONT_SECTION
            self.sheet.text(x + 7, self.y + 23, value, value_size)
        self.sheet.rule(x0, self.y + 54, right - 1, 1)
        self.y += height

    def add_index_grid(self, indices: List[dict]):
        for row_start in range(0, len(indices), 2):
            row = indices[row_start:row_start + 2]
            self.ensure(44)
            for column, item in enumerate(row):
                x = PAGE_MARGIN + column * (COLUMN_W + COLUMN_GAP)
                raw_name = str(item.get("name") or "--")
                name = (raw_name.replace("上证指数", "上证").replace("深证成指", "深证")
                        .replace("创业板指", "创业板").replace("恒生指数", "恒生")
                        .replace("恒生科技指数", "恒生科技"))
                close = item.get("close")
                close_text = f"{float(close):,.2f}" if isinstance(close, (int, float)) else "--"
                pct = fmt_pct(item.get("pct"))
                self.sheet.text(x, self.y + 1, name, FONT_META)
                self.sheet.text(x + COLUMN_W - self.sheet.measure(pct, FONT_META),
                                self.y + 2, pct, FONT_META)
                close_size = FONT_TITLE if self.sheet.measure(close_text, FONT_TITLE) <= COLUMN_W else FONT_SECTION
                self.sheet.text(x, self.y + 18, close_text, close_size)
            self.sheet.rule(PAGE_MARGIN, self.y + 42, WIDTH - PAGE_MARGIN - 1, 1)
            self.y += 44


class NewsFlow(PageFlow):
    """双栏快讯流：短条目整块移动，长条目按行续排，绝不省略结尾。"""

    def __init__(self, *args, **kwargs):
        self.column = 0
        self.page_items: List[dict] = []
        self.has_split_item = False
        self.dedicated_page = False
        super().__init__(*args, **kwargs)

    def new_page(self):
        self.column = 0
        self.page_items = []
        self.has_split_item = False
        self.dedicated_page = False
        super().new_page()

    def _advance(self):
        if self.column == 0:
            self.column = 1
            self.y = BODY_TOP
        else:
            self.new_page()

    def _draw_whole_item(self, item: dict, column: int, y: int):
        x = PAGE_MARGIN + column * (COLUMN_W + COLUMN_GAP)
        self.sheet.text(x, y + 1, f"{item['number']:03d}", FONT_MIN)
        for line in item["lines"]:
            self.sheet.text(x + NEWS_NUMBER_W, y, line, FONT_SMALL, True)
            y += LEADING_SMALL
        y += 7
        if y < BODY_BOTTOM:
            self.sheet.rule(x, y - 4, x + COLUMN_W - 5, 1)

    def _add_dedicated_long_item(self, number: int, lines: List[str]):
        """超长标题独占页面，并按行数均衡双栏，避免下一页只孤零零剩一行。"""
        per_column = BODY_HEIGHT // LEADING_SMALL
        per_page = per_column * 2
        page_count = math.ceil(len(lines) / per_page)
        offset = 0
        for chunk_index in range(page_count):
            # 所有页均匀分配，而不是前 N-1 页塞满、末页只剩一两行。
            chunk_size = len(lines) // page_count + int(chunk_index < len(lines) % page_count)
            chunk = lines[offset:offset + chunk_size]
            offset += chunk_size
            if chunk_index:
                self.new_page()
            left_count = (len(chunk) + 1) // 2
            columns = [chunk[:left_count], chunk[left_count:]]
            for column, column_lines in enumerate(columns):
                x = PAGE_MARGIN + column * (COLUMN_W + COLUMN_GAP)
                y = BODY_TOP
                if column == 0 and chunk_index == 0:
                    self.sheet.text(x, y + 1, f"{number:03d}", FONT_MIN)
                for line in column_lines:
                    self.sheet.text(x + NEWS_NUMBER_W, y, line, FONT_SMALL, True)
                    y += LEADING_SMALL
                if y + 3 < BODY_BOTTOM:
                    self.sheet.rule(x, y + 2, x + COLUMN_W - 5, 1)
            self.has_split_item = True
            self.dedicated_page = True
            self.column = 1
            self.y = BODY_TOP + len(columns[1]) * LEADING_SMALL

    def add_item(self, number: int, title: str):
        if self.dedicated_page:
            self.new_page()
        lines = self.sheet.wrap_all(title, FONT_SMALL, COLUMN_W - NEWS_NUMBER_W, True)
        if not lines:
            return
        item_height = len(lines) * LEADING_SMALL + 7
        if item_height > BODY_HEIGHT:
            if self.page_items or self.column != 0 or self.y > BODY_TOP:
                self.balance_last_page()
                self.new_page()
            self._add_dedicated_long_item(number, lines)
            return
        if self.y + item_height > BODY_BOTTOM:
            self._advance()
        item = {"number": number, "lines": lines, "height": item_height}
        self._draw_whole_item(item, self.column, self.y)
        self.page_items.append(item)
        self.y += item_height

    def balance_last_page(self):
        """末页按实际行高均衡左右栏；有跨栏长标题时保留原阅读顺序。"""
        if self.has_split_item or len(self.page_items) < 2:
            return
        heights = [item["height"] for item in self.page_items]
        best = None
        for split in range(1, len(self.page_items)):
            left = sum(heights[:split])
            right = sum(heights[split:])
            if left <= BODY_HEIGHT and right <= BODY_HEIGHT:
                score = abs(left - right) + (2 if left < right else 0)
                if best is None or score < best[0]:
                    best = (score, split)
        if best is None:
            return
        split = best[1]
        columns = [self.page_items[:split], self.page_items[split:]]
        # finalize() 尚未绘制页眉页脚；只清理正文，文字测量缓存继续复用。
        self.sheet.draw.rectangle((0, BODY_TOP, WIDTH - 1, FOOTER_RULE_Y - 1), fill=WHITE)
        self.sheet.text_runs = [r for r in self.sheet.text_runs if r["region"] != "body"]
        for column, items in enumerate(columns):
            y = BODY_TOP
            for item in items:
                self._draw_whole_item(item, column, y)
                y += item["height"]
        self.column = 1
        self.y = sum(item["height"] for item in columns[1]) + BODY_TOP


class HeatFlow(NewsFlow):
    """热力图双栏数据卡：全部关键词保留，删去重复的长产品说明。"""

    def new_page(self):
        self.columns_top = BODY_TOP
        super().new_page()

    def begin_cards(self):
        self.columns_top = self.y

    def _advance(self):
        if self.column == 0:
            self.column = 1
            self.y = self.columns_top
        else:
            self.new_page()

    @staticmethod
    def _etf_codes(keyword: dict) -> str:
        pair = keyword.get("etf_pair") or []
        codes = [str(item.get("code") or "") for item in pair if isinstance(item, dict)]
        codes = [code for code in codes if code]
        if codes:
            return " × ".join(codes[:2])
        return str(keyword.get("concept") or "").replace("港股 ETF 双标的：", "")

    def add_card(self, keyword: dict):
        tag = str(keyword.get("display") or keyword.get("tag") or keyword.get("keyword") or "未命名")
        heat = _int(round(float(keyword.get("heat") or keyword.get("mentions") or 0)))
        net = _int(keyword.get("net"))
        sentiment = str(keyword.get("sentiment_label") or keyword.get("sentiment") or "中性")
        etf = self._etf_codes(keyword)
        title_lines = self.sheet.wrap_all(tag, FONT_BODY, COLUMN_W - 43, True)
        meta = f"{net:+d} · {sentiment}"
        separate_etf = bool(etf) and self.sheet.measure(meta + etf, FONT_MIN) + 12 > COLUMN_W
        height = len(title_lines) * 18 + LEADING_MIN + (LEADING_MIN if separate_etf else 0) + 9
        if height > BODY_HEIGHT:
            raise LayoutError(f"单个热力关键词超过整页：{tag[:30]}")
        if self.y + height > BODY_BOTTOM:
            self._advance()
        x = PAGE_MARGIN + self.column * (COLUMN_W + COLUMN_GAP)
        heat_text = f"{heat:02d}"
        self.sheet.text(x + COLUMN_W - self.sheet.measure(heat_text, FONT_TITLE),
                        self.y, heat_text, FONT_TITLE)
        for line in title_lines:
            self.sheet.text(x, self.y, line, FONT_BODY, True)
            self.y += 18
        self.sheet.text(x, self.y, meta, FONT_MIN)
        if etf and not separate_etf:
            self.sheet.text(x + COLUMN_W - self.sheet.measure(etf, FONT_MIN), self.y, etf, FONT_MIN)
        self.y += LEADING_MIN
        if etf and separate_etf:
            self.sheet.text(x, self.y, etf, FONT_MIN)
            self.y += LEADING_MIN
        rule_right = x + COLUMN_W - 5
        self.sheet.rule(x, self.y + 2, rule_right, 1)
        heat_x = x + round((rule_right - x) * max(0, min(100, heat)) / 100)
        self.sheet.rule(x, self.y + 2, heat_x, 2)
        self.y += 9


def _page_count_delta(renderer: MultiPageRenderer, before: int, page_num: int) -> int:
    return page_num + len(renderer.pages) - before


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def draw_daily_summary_pages(renderer: MultiPageRenderer, data: dict, now: datetime,
                             page_num: int, total_estimate: int) -> int:
    before = len(renderer.pages)
    summary = data.get("summary") or {}
    bias = str(summary.get("bias") or "中性")
    bull = _int(summary.get("bull"), 50)
    bear = 100 - bull
    signals = _int(summary.get("signals"))
    flow = PageFlow(renderer, "AI 每日总结", f"今日判断 · {bias}")
    flow.add_text(str(summary.get("headline") or "暂无市场总结"), FONT_SECTION, 21, gap=10)
    flow.add_metrics([
        ("市场情绪", bias),
        ("多方 / 空方", f"{bull}% / {bear}%"),
        ("信号样本", str(signals)),
    ])
    up = "、".join(summary.get("sectors_up") or [])
    down = "、".join(summary.get("sectors_down") or [])
    sector_text = "｜".join(filter(None, [f"利好 {up}" if up else "", f"承压 {down}" if down else ""]))
    flow.add_entry("板块方向", sector_text or "暂无明确方向", FONT_BODY)
    flow.add_entry("资金流向", str(summary.get("flow") or "暂无资金流向信号"), FONT_BODY)
    return _page_count_delta(renderer, before, page_num)


def _market_fallback_points(review: dict) -> List[dict]:
    points: List[dict] = []
    for key, label in [
        ("turnover_text", "成交额"), ("breadth_text", "市场广度"),
        ("sectors_text", "板块表现"), ("funds_text", "资金流向"),
        ("outlook", "后市观点"),
    ]:
        if review.get(key):
            points.append({"label": label, "text": str(review[key])})
    if points:
        return points
    turnover = review.get("turnover") or {}
    if turnover.get("amount") is not None:
        points.append({"label": "成交额", "text": f"成交 {fmt_amount(turnover['amount'])}"})
    breadth = review.get("breadth") or {}
    parts = []
    for key, label in [("up", "上涨"), ("down", "下跌"), ("limit_up", "涨停"), ("limit_down", "跌停")]:
        if breadth.get(key) is not None:
            parts.append(f"{label} {breadth[key]}")
    if parts:
        points.append({"label": "市场广度", "text": " · ".join(parts)})
    for key, label, limit in [("leaders", "领涨", 3), ("laggards", "领跌", 3)]:
        names = [str(x.get("name") or "") for x in (review.get(key) or [])[:limit]]
        names = [name for name in names if name]
        if names:
            points.append({"label": label, "text": "、".join(names)})
    return points


def draw_market_pages(renderer: MultiPageRenderer, review: dict | None, market_name: str,
                      page_num: int, total_estimate: int) -> int:
    before = len(renderer.pages)
    date = str((review or {}).get("date") or "日期待定")
    flow = PageFlow(renderer, f"AI 看盘 · {market_name}", date)
    if not review:
        flow.add_entry("STATUS", "今日未获取行情，等待数据源恢复。", FONT_BODY)
        return _page_count_delta(renderer, before, page_num)
    flow.add_text(str(review.get("headline") or "暂无行情复盘"), FONT_SMALL, LEADING_SMALL, gap=4)
    flow.add_index_grid(list(review.get("indices") or [])[:6])
    points = [p for p in (review.get("points") or []) if p.get("key") != "indices"]
    if not points:
        points = _market_fallback_points(review)
    for point in points:
        text = str(point.get("text") or "")
        if text:
            # 标准六维复盘固定在两页展开：第一页指数/成交/广度，第二页板块/资金/策略。
            if point.get("key") == "sectors" and flow.y > BODY_TOP:
                flow.new_page()
            label = str(point.get("label") or "市场观察")
            flow.add_rich([
                {"text": f"{label}｜{text}", "size": FONT_SMALL, "leading": LEADING_SMALL},
            ], gap=0)
    return _page_count_delta(renderer, before, page_num)


def draw_heatmap_pages(renderer: MultiPageRenderer, heatmap: dict | None,
                       page_num: int, total_estimate: int) -> int:
    before = len(renderer.pages)
    keywords = (heatmap or {}).get("keywords") or (heatmap or {}).get("items") or []
    if isinstance(keywords, dict):
        keywords = list(keywords.values())
    flow = HeatFlow(renderer, "AI 情绪热力图", f"HEAT / {len(keywords)} 关键词 · 净信号 / ETF")
    if not heatmap:
        flow.add_entry("STATUS", "暂无热力图数据，不做无依据推演。", FONT_BODY)
        return _page_count_delta(renderer, before, page_num)
    # 长总结已在「每日总结」呈现；热力页保留全部关键词，以纯数据跨页展示。
    if not keywords:
        flow.add_entry("关键词", str(heatmap.get("headline") or "当日样本暂无关键词命中。"), FONT_BODY)
    flow.begin_cards()
    for keyword in keywords:
        if isinstance(keyword, dict):
            flow.add_card(keyword)
        else:
            flow.add_item(0, str(keyword))
    return _page_count_delta(renderer, before, page_num)


def draw_policy_pages(renderer: MultiPageRenderer, policy: dict | None,
                      page_num: int, total_estimate: int) -> int:
    before = len(renderer.pages)
    flow = PageFlow(renderer, "AI 政策分析", str((policy or {}).get("stance") or "政策信号"))
    if not policy:
        flow.add_entry("STATUS", "暂无政策信号，等待数据源恢复。", FONT_BODY)
        return _page_count_delta(renderer, before, page_num)
    flow.add_text(str(policy.get("headline") or "政策分析"), FONT_SMALL, LEADING_SMALL, gap=6)
    buckets = policy.get("buckets") or policy.get("sectors") or []
    if isinstance(buckets, dict):
        buckets = list(buckets.values())
    if not buckets:
        flow.add_entry("政策维度", "当日样本中暂无政策面信号可统计。", FONT_BODY)
    for index, bucket in enumerate(buckets[:8], 1):
        if isinstance(bucket, dict):
            tag = str(bucket.get("tag") or bucket.get("label") or bucket.get("dimension") or "未命名")
            net = _int(bucket.get("net") if bucket.get("net") is not None else bucket.get("policy_net"))
            sources_hit = _int(bucket.get("sources") if bucket.get("sources") is not None else bucket.get("sources_hit"))
            mentions = _int(bucket.get("mentions"))
            stance = str(bucket.get("stance") or "中性")
            flow.add_rich([
                {"text": f"{index:02d}  {tag}   净{net:+d}", "size": FONT_BODY,
                 "leading": LEADING_BODY, "bold": True},
                {"text": f"{stance} · {sources_hit}源命中 · {mentions}条提及",
                 "size": FONT_META, "leading": LEADING_META, "indent": 24},
            ], gap=3, separator=True)
        else:
            flow.add_text(f"{index:02d}  {bucket}", FONT_SMALL, LEADING_SMALL, bold=True)
    return _page_count_delta(renderer, before, page_num)


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _text_item(value, key: str = "industry", limit: int = 0) -> str:
    """把深度层里的「字符串 / 字典」条目统一成一行文本。

    002 的政策条目既可能是纯字符串，也可能是 ``{industry, note/driver/impact, ...}``；
    这里按 ``key`` 优先取主字段，再拼上其余非空短字段，避免直接打印成 ``{'a': 'b'}``。
    """
    if isinstance(value, dict):
        parts: List[str] = []
        primary = _clip(value.get(key) or value.get("industry") or value.get("tag"), limit)
        if primary:
            parts.append(primary)
        for field in ("direction", "nature", "horizon", "note", "impact", "driver"):
            if field == key:
                continue
            text = _clip(value.get(field), limit or 160)
            if text:
                parts.append(text)
        return " · ".join(parts)
    return _clip(value, limit)


def _clip(text, limit: int = 0) -> str:
    """压缩空白；``limit>0`` 时按字符截断并补省略号（仅用于证据类长文，标题不截）。"""
    value = " ".join(str(text or "").split())
    if limit and len(value) > limit:
        return value[: max(1, limit - 1)] + "…"
    return value


def draw_policy_deep_pages(renderer: MultiPageRenderer, deep: dict | None,
                           page_num: int, total_estimate: int) -> int:
    """「AI 政策深度」：知识库历史对照 + 舆情/术语口径 + 传导图谱 + 四步推理链。

    对应 002 网页的同名栏目；全部字段来自 ``sources.analyze_policy_deep()``。
    """
    before = len(renderer.pages)
    if not deep:
        return _page_count_delta(renderer, before, page_num)

    kb = deep.get("kb") or {}
    modifiers = deep.get("modifiers") or {}
    sentiment = deep.get("sentiment") or {}
    graph = deep.get("graph") or {}
    reasoning = deep.get("reasoning") or {}

    # ── 知识库检索与历史对照
    hits = [hit for hit in (kb.get("hits") or []) if isinstance(hit, dict)]
    flow = PageFlow(renderer, "AI 政策深度", "知识库对照" if hits else "知识库")
    if not hits:
        flow.add_entry("STATUS", "当日未检索到可对照的历史政策条目。", FONT_BODY)
    if kb.get("note"):
        flow.add_text(_clip(kb.get("note")), FONT_META, LEADING_META, gap=4)
    for index, hit in enumerate(hits[:3], 1):
        title = _clip(hit.get("title"), 40)
        issuer = _clip(hit.get("issuer") or hit.get("dimension"), 24)
        flow.add_rich([
            {"text": f"{index:02d}  {_clip(hit.get('date'), 12)}  {issuer}", "size": FONT_BODY,
             "leading": LEADING_BODY, "bold": True},
            {"text": f"{_clip(hit.get('doctype'), 10)}《{title}》", "size": FONT_META,
             "leading": LEADING_META, "indent": 24},
            {"text": _clip(hit.get("market_effect") or hit.get("summary")), "size": FONT_MIN,
             "leading": LEADING_MIN, "indent": 24},
        ], gap=3, separator=True)
    for comparison in [c for c in (kb.get("comparison") or []) if c][:3]:
        flow.add_text(_clip(comparison), FONT_MIN, LEADING_MIN, gap=4)

    # ── 舆情情感与修饰词术语口径
    flow = PageFlow(renderer, "AI 政策深度", "舆情 · 术语口径")
    for label, key in (("综合", "overall"), ("官媒", "official"), ("市场", "market")):
        bucket = sentiment.get(key) or {}
        if not bucket:
            continue
        flow.add_rich([
            {"text": f"{label}  {bucket.get('label') or '中性'}  {_num(bucket.get('score')):+.2f}",
             "size": FONT_BODY, "leading": LEADING_BODY, "bold": True},
            {"text": (f"正向 {_int(bucket.get('positive'))} · 负向 {_int(bucket.get('negative'))}"
                      f" · 中性 {_int(bucket.get('neutral'))} · 合计 {_int(bucket.get('total'))}"),
             "size": FONT_META, "leading": LEADING_META, "indent": 24},
        ], gap=2, separator=True)
    if sentiment.get("gap") is not None:
        flow.add_text(f"官媒与市场解读差 {_num(sentiment.get('gap')):+.2f}"
                      "（正值＝官方口径更乐观）", FONT_META, LEADING_META, gap=5)
    items = [item for item in (sentiment.get("items") or []) if isinstance(item, dict)]
    for item in items[:5]:
        source = _clip(item.get("source"), 12)
        flow.add_text(f"· {source}｜{_clip(item.get('title'), 60)}", FONT_MIN, LEADING_MIN, gap=2)
    for index, hit in enumerate([h for h in (modifiers.get("hits") or []) if isinstance(h, dict)][:3], 1):
        flow.add_rich([
            {"text": f"{index:02d}  「{_clip(hit.get('word'), 12)}」  强度 "
                     f"{_num(hit.get('strength')):+.1f}", "size": FONT_BODY,
             "leading": LEADING_BODY, "bold": True},
            {"text": _clip(hit.get("sense")), "size": FONT_MIN, "leading": LEADING_MIN, "indent": 24},
            {"text": _clip(hit.get("market")), "size": FONT_MIN, "leading": LEADING_MIN, "indent": 24},
        ], gap=2, separator=True)

    # ── 政策传导图谱
    chains = [c for c in (graph.get("chains") or []) if isinstance(c, dict)]
    flow = PageFlow(renderer, "AI 政策深度", "政策传导图谱")
    if not chains:
        flow.add_entry("STATUS", "当日暂无政策传导链样本。", FONT_BODY)
    for index, chain in enumerate(chains[:4], 1):
        industries = "、".join(str(x) for x in (chain.get("industries") or [])[:5])
        flow.add_rich([
            {"text": f"{index:02d}  {_clip(chain.get('bucket'), 20)}  "
                     f"{chain.get('direction') or '中性'}净{_int(chain.get('net')):+d}",
             "size": FONT_BODY, "leading": LEADING_BODY, "bold": True},
            {"text": f"{_clip(chain.get('subject'), 24)} → {industries}", "size": FONT_META,
             "leading": LEADING_META, "indent": 24},
            {"text": "上游 " + "、".join(str(x) for x in (chain.get("upstream") or [])[:4]),
             "size": FONT_MIN, "leading": LEADING_MIN, "indent": 24},
            {"text": "中游 " + "、".join(str(x) for x in (chain.get("midstream") or [])[:4]),
             "size": FONT_MIN, "leading": LEADING_MIN, "indent": 24},
            {"text": "下游 " + "、".join(str(x) for x in (chain.get("downstream") or [])[:4]),
             "size": FONT_MIN, "leading": LEADING_MIN, "indent": 24},
            {"text": (f"{_clip(chain.get('nature'), 8)} · {_clip(chain.get('horizon'), 12)} · "
                      f"{_int(chain.get('mentions'))}条提及 · {_int(chain.get('sources'))}源命中"),
             "size": FONT_MIN, "leading": LEADING_MIN, "indent": 24},
        ], gap=2, separator=True)

    # ── 四步推理链
    steps = [step for step in (reasoning.get("steps") or []) if isinstance(step, dict)]
    flow = PageFlow(renderer, "AI 政策深度",
                    f"推理链 · 置信度 {_clip(reasoning.get('confidence'), 6) or '—'}")
    if not steps:
        flow.add_entry("STATUS", "当日暂无推理链样本。", FONT_BODY)
    for step in steps[:5]:
        evidence = [e for e in (step.get("evidence") or []) if isinstance(e, dict)]
        paragraphs = [
            {"text": f"{_clip(step.get('no'), 3) or '—'}  {_clip(step.get('label'), 12)}",
             "size": FONT_BODY, "leading": LEADING_BODY, "bold": True},
            {"text": _clip(step.get("text")), "size": FONT_MIN, "leading": LEADING_MIN, "indent": 24},
        ]
        if evidence:
            item = evidence[0]
            prefix = _clip(item.get("source"), 12)
            paragraphs.append({"text": f"· {prefix}｜{_clip(item.get('text'), 70)}",
                               "size": FONT_MIN, "leading": LEADING_MIN, "indent": 24})
        flow.add_rich(paragraphs, gap=3, separator=True)
    return _page_count_delta(renderer, before, page_num)


def draw_research_pages(renderer: MultiPageRenderer, deep: dict | None,
                        page_num: int, total_estimate: int) -> int:
    """「AI 政策研报」：结论与受益/承压方向 + 长逻辑与风险 + 时序模型读数。"""
    before = len(renderer.pages)
    research = (deep or {}).get("research") or {}
    models = (deep or {}).get("models") or {}
    if not research and not models:
        return _page_count_delta(renderer, before, page_num)

    flow = PageFlow(renderer, "AI 政策研报", _clip(research.get("rating"), 14) or "政策影响研报")
    if research:
        flow.add_text(_clip(research.get("title")), FONT_BODY, LEADING_BODY, bold=True, gap=4)
        if research.get("rating_note"):
            flow.add_text(_clip(research.get("rating_note")), FONT_META, LEADING_META, gap=4)
        if research.get("abstract"):
            flow.add_text(_clip(research.get("abstract")), FONT_MIN, LEADING_MIN, gap=5)
        if research.get("horizon"):
            flow.add_text("周期 " + _clip(research.get("horizon"), 60), FONT_MIN, LEADING_MIN, gap=4)
        beneficiaries = [_text_item(x) for x in (research.get("beneficiaries") or [])]
        pressured = [_text_item(x) for x in (research.get("pressured") or [])]
        beneficiaries = [x for x in beneficiaries if x]
        pressured = [x for x in pressured if x]
        if beneficiaries:
            flow.add_text("受益 " + "、".join(beneficiaries[:5]), FONT_MIN, LEADING_MIN, gap=2)
        if pressured:
            flow.add_text("承压 " + "、".join(pressured[:5]), FONT_MIN, LEADING_MIN, gap=4)
        for index, point in enumerate([x for x in (research.get("long_term") or []) if x][:4], 1):
            flow.add_rich([
                {"text": f"{index:02d}  {_text_item(point, 'industry', 40)}", "size": FONT_META,
                 "leading": LEADING_META, "bold": True},
                {"text": _text_item(point, "impact", 160) or _text_item(point, "driver", 120),
                 "size": FONT_MIN, "leading": LEADING_MIN, "indent": 24},
            ], gap=2)
        for risk in [x for x in (research.get("risks") or []) if x][:3]:
            flow.add_text("风险 · " + _clip(risk), FONT_MIN, LEADING_MIN, gap=2)
        if research.get("watch_window"):
            flow.add_text("窗口 " + _clip(research.get("watch_window"), 90), FONT_MIN,
                          LEADING_MIN, gap=2)

    if models:
        series = models.get("series") or {}
        flow = PageFlow(renderer, "AI 政策研报",
                        "时序模型 · " + (_clip(series.get("name"), 12) or "序列"))
        if series:
            flow.add_text(f"序列 {_clip(series.get('name'), 20)} · 来源 "
                          f"{_clip(series.get('source'), 10)} · {_int(series.get('points'))} 个交易日",
                          FONT_META, LEADING_META, gap=4)
            if series.get("note"):
                flow.add_text(_clip(series.get("note")), FONT_MIN, LEADING_MIN, gap=5)
        if models.get("shock") is not None:
            flow.add_text(f"政策情景偏移 {_num(models.get('shock')):+.2f}"
                          "（负值＝偏紧情景，压制估值）", FONT_META, LEADING_META, gap=5)
        prophet = models.get("prophet") or {}
        if prophet:
            flow.add_rich([
                {"text": "Prophet 式分解", "size": FONT_BODY, "leading": LEADING_BODY, "bold": True},
                {"text": _clip(prophet.get("summary")), "size": FONT_MIN,
                 "leading": LEADING_MIN, "indent": 24},
            ], gap=3, separator=True)
        lstm = models.get("lstm") or {}
        if lstm:
            flow.add_rich([
                {"text": f"LSTM · {_clip(lstm.get('direction'), 8) or '—'}",
                 "size": FONT_BODY, "leading": LEADING_BODY, "bold": True},
                {"text": _clip(lstm.get("summary")), "size": FONT_MIN,
                 "leading": LEADING_MIN, "indent": 24},
            ], gap=3, separator=True)
    return _page_count_delta(renderer, before, page_num)


def _sector_paragraphs(item: dict, index: int, pressure: bool = False) -> List[dict]:
    tag = str(item.get("tag") or "未命名")
    net = _int(item.get("net"))
    sources_hit = _int(item.get("sources"))
    mentions = _int(item.get("mentions"))
    direction = "承压" if pressure else "机会"
    paragraphs = [
        {"text": f"{index:02d}  {tag}   {direction}净{net:+d}", "size": FONT_SECTION,
         "leading": 20, "bold": True},
        {"text": f"{sources_hit}源命中 · {mentions}条提及", "size": FONT_META,
         "leading": LEADING_META, "indent": 24},
    ]
    etf_text = HeatFlow._etf_codes(item)
    if etf_text:
        paragraphs.append({"text": f"ETF  {etf_text}", "size": FONT_MIN,
                           "leading": LEADING_MIN, "indent": 24})
    # 每个方向保留一条代表证据；其余证据与完整 ETF 说明仍在网页简报。
    for evidence in (item.get("evidence") or [])[:1]:
        title = " ".join(str(evidence.get("title") or "").split())
        if title:
            source = str(evidence.get("source") or "").strip()
            prefix = f"· {source}｜" if source else "· "
            paragraphs.append({"text": prefix + title, "size": FONT_MIN,
                               "leading": LEADING_MIN, "indent": 24})
    return paragraphs


def draw_sectors_pages(renderer: MultiPageRenderer, summary: dict,
                       page_num: int, total_estimate: int) -> int:
    before = len(renderer.pages)
    opportunities = summary.get("opportunity_sectors") or []
    pressures = summary.get("pressure_sectors") or []
    flow = PageFlow(renderer, "AI 板块机会", f"机会 {len(opportunities)} · 承压 {len(pressures)}")
    if not opportunities and not pressures:
        flow.add_entry("板块方向", "今日样本中暂无净多头或净空头信号占优的板块。", FONT_BODY)
    for index, item in enumerate(opportunities[:4], 1):
        flow.add_rich(_sector_paragraphs(item, index), gap=2, separator=True)
    for index, item in enumerate(pressures[:2], 1):
        flow.add_rich(_sector_paragraphs(item, index, pressure=True), gap=2, separator=True)
    return _page_count_delta(renderer, before, page_num)


def _section_labels() -> Dict[str, str]:
    sections = getattr(sources, "SECTIONS", [])
    return {s["key"]: s["label"] for s in sections} if sections else {}


def _source_section(name: str, section_map: Dict[str, str]) -> str:
    try:
        meta = sources.source_meta(name)
    except Exception:
        meta = None
    section_key = (meta.get("section") if meta else "finance") or "finance"
    return section_map.get(section_key, section_key)


def _news_entries(brief: dict) -> List[tuple]:
    """全量快讯池：``[(板块, 来源, 来源内序号, 标题), ...]``，按来源轮转排序。

    先排各来源的第 1 条，再排各来源的第 2 条……因此无论全量还是按页位截断，
    结果都保持「各源均衡」，不会把靠前的几个来源排满、后面的整段丢掉。
    ``brief`` 内跨来源重复标题只保留最先出现的一条。
    """
    section_map = _section_labels()
    seen = set()
    queues: List[tuple] = []
    for name, items in (brief or {}).items():
        titles: List[str] = []
        for item in items or []:
            title = " ".join(str(item.get("title", "")).split())
            key = title.lower()
            if title and key not in seen:
                seen.add(key)
                titles.append(title)
        if titles:
            queues.append((_source_section(name, section_map), name, titles))
    entries: List[tuple] = []
    depth = 0
    while True:
        added = False
        for label, name, titles in queues:
            if depth < len(titles):
                entries.append((label, name, depth, titles[depth]))
                added = True
        if not added:
            return entries
        depth += 1


def _news_pool(brief: dict) -> List[tuple]:
    """``[(板块, 标题), ...]`` 轮转顺序的全量快讯池。"""
    return [(label, title) for label, _name, _index, title in _news_entries(brief)]


def _group_news(brief: dict, per_source: int = NEWS_PER_SOURCE_FULL) -> Dict[str, List[str]]:
    """按板块分组快讯标题。

    ``per_source`` 为 :data:`NEWS_PER_SOURCE_FULL`（0）或负数时表示全量：
    抓到的每个标题都排版，不按来源截断；否则每个来源只取前 ``per_source`` 条。
    板块内顺序与 :func:`_news_pool` 一致（各源轮转），截断时同样各源均衡。
    """
    full = per_source is None or per_source <= 0
    grouped: Dict[str, List[str]] = {}
    for label, _name, index, title in _news_entries(brief):
        if full or index < per_source:
            grouped.setdefault(label, []).append(title)
    return {label: titles for label, titles in grouped.items() if titles}


def draw_news_pages(renderer: MultiPageRenderer, brief: dict, page_num: int,
                    total_estimate: int,
                    news_per_source: int = NEWS_PER_SOURCE_FULL,
                    news_limit: int | None = None) -> int:
    """排版「全网快讯」。

    - ``news_limit`` 给定时：从全量快讯池按各源轮转顺序取前 ``news_limit`` 条（页位用满即止）。
    - 否则 ``news_per_source<=0`` 表示全量推送；``>0`` 表示每源精选 N 条。
    """
    before = len(renderer.pages)
    full = news_per_source is None or news_per_source <= 0
    pool = _news_pool(brief)
    available_count = len(pool)
    if full:
        kept_pool = pool if news_limit is None else pool[:max(0, news_limit)]
        grouped: Dict[str, List[str]] = {}
        for label, title in kept_pool:
            grouped.setdefault(label, []).append(title)
    else:
        grouped = _group_news(brief, news_per_source)
    included_count = sum(len(titles) for titles in grouped.values())
    renderer.layout_meta.update({
        "news_mode": "full" if full else f"per_source_{news_per_source}",
        "news_per_source": None if full else news_per_source,
        "news_full": full and included_count >= available_count,
        "news_limit": None if (not full or news_limit is None) else news_limit,
        "news_available": available_count,
        "news_included": included_count,
        "news_omitted": max(0, available_count - included_count),
    })
    if full and news_limit is not None and news_limit <= 0:
        # 页位全部分给分析栏目：本栏不占页，避免用「暂无快讯」白占一页。
        return _page_count_delta(renderer, before, page_num)
    if not grouped:
        flow = PageFlow(renderer, "全网快讯", "暂无快讯")
        flow.add_entry("STATUS", "今日未获取可显示的快讯标题。", FONT_BODY)
        return _page_count_delta(renderer, before, page_num)

    number = 1
    for label, titles in grouped.items():
        if full:
            edition = (f" · 全量{len(titles)}条" if included_count >= available_count
                       else f" · 全量{available_count}条取{included_count}条")
        elif news_per_source < 3:
            edition = f" · 精选{news_per_source}条/源"
        else:
            edition = f" · {len(titles)}条"
        flow = NewsFlow(renderer, "全网快讯", label + edition)
        for title in titles:
            flow.add_item(number, title)
            number += 1
        flow.balance_last_page()
    return _page_count_delta(renderer, before, page_num)


def draw_all_pages(data: dict, now: datetime, live: int, total: int, demo: bool,
                   news_per_source: int = NEWS_PER_SOURCE_FULL,
                   news_limit: int | None = None) -> MultiPageRenderer:
    renderer = MultiPageRenderer(FONT_PATH)
    page_num = 1
    page_num = draw_daily_summary_pages(renderer, data, now, page_num, 0)
    page_num = draw_market_pages(renderer, data.get("review_ashare"), "A股", page_num, 0)
    page_num = draw_market_pages(renderer, data.get("review_hk"), "港股", page_num, 0)
    page_num = draw_market_pages(renderer, data.get("review_us"), "美股", page_num, 0)
    page_num = draw_heatmap_pages(renderer, data.get("heatmap"), page_num, 0)
    page_num = draw_policy_pages(renderer, data.get("policy"), page_num, 0)
    page_num = draw_policy_deep_pages(renderer, data.get("policy_deep_layer"), page_num, 0)
    page_num = draw_research_pages(renderer, data.get("policy_deep_layer"), page_num, 0)
    page_num = draw_sectors_pages(renderer, data.get("summary") or {}, page_num, 0)
    draw_news_pages(renderer, data.get("brief") or {}, page_num, 0,
                    news_per_source=news_per_source, news_limit=news_limit)
    if demo and renderer.pages:
        renderer.pages[0].page_subtitle += " · 离线演示"
    renderer.finalize(now)
    return renderer


def _build_pages(data: dict, now: datetime, live: int, total: int, demo: bool,
                 max_pages: int, news_per_source: int, news_limit: int | None,
                 cache: dict) -> MultiPageRenderer:
    key = (news_per_source, news_limit)
    if key not in cache:
        renderer = draw_all_pages(data, now, live, total, demo,
                                  news_per_source=news_per_source, news_limit=news_limit)
        renderer.page_limit = max_pages
        cache[key] = renderer
    return cache[key]


def render_with_page_budget(data: dict, now: datetime, live: int, total: int, demo: bool,
                            max_pages: int = MAX_PAGES,
                            news_per_source: int | None = None) -> MultiPageRenderer:
    """默认全量推送快讯，把固件页位用满；显式给定每源条数时按该口径精选。

    全量模式下先排分析栏目，再用二分求得「页数不超过上限」的最大快讯条数：
    每多留一条快讯就多推进一页，直到 48 页上限为止，各来源始终均衡。
    显式 ``news_per_source>0`` 时只按该口径出图，放不下才沿阶梯向下收敛。
    """
    max_pages = max(1, min(MAX_PAGES, max_pages or MAX_PAGES))
    brief = data.get("brief") or {}
    cache: dict = {}
    pool_size = len(_news_pool(brief))

    if news_per_source is None or news_per_source <= 0:
        # 先只排分析栏目：若连它都放不下，就没有可裁的快讯版位了。
        analysis_only = _build_pages(data, now, live, total, demo, max_pages,
                                     NEWS_PER_SOURCE_FULL, 0, cache)
        if len(analysis_only.pages) > max_pages:
            raise LayoutError(
                f"分析栏目本身需 {len(analysis_only.pages)} 页（上限 {max_pages}）；"
                "已停止生成，未裁切正文或覆盖上一版"
            )
        best = analysis_only
        # 快讯条数越多页数不减，因此可以直接二分「放得下的最大条数」，把页位用满。
        low, high = 0, pool_size
        while low <= high:
            mid = (low + high) // 2
            candidate = _build_pages(data, now, live, total, demo, max_pages,
                                     NEWS_PER_SOURCE_FULL, mid, cache)
            if len(candidate.pages) <= max_pages:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        best.validate(max_pages)
        included = best.layout_meta.get("news_included", 0)
        if included >= pool_size:
            print(f"全量推送：快讯 {included}/{pool_size} 条全部排版，共 {len(best.pages)}/{max_pages} 页")
        else:
            print(f"全量推送：快讯 {included}/{pool_size} 条排出 {len(best.pages)}/{max_pages} 页，"
                  f"其余 {pool_size - included} 条留在网页研报，分析栏目与关键结论不裁剪")
        return best

    ladder = [news_per_source] + [n for n in NEWS_PER_SOURCE_LADDER if n < news_per_source]
    last_count = 0
    for quota in ladder:
        renderer = _build_pages(data, now, live, total, demo, max_pages,
                                quota, None, cache)
        last_count = len(renderer.pages)
        if last_count <= max_pages:
            renderer.validate(max_pages)
            included = renderer.layout_meta.get("news_included", 0)
            if quota != news_per_source:
                print(f"版位自适应：每源 {news_per_source} 条需超过 {max_pages} 页，"
                      f"收敛为每源 {quota} 条（{included} 条），分析栏目与关键结论保留，"
                      f"共 {last_count} 页")
            return renderer
        print(f"排版预检：每源 {quota} 条快讯需 {last_count} 页，超过 {max_pages} 页，调整快讯版位")
    raise LayoutError(
        f"分析栏目与每源 1 条快讯仍需 {last_count} 页（上限 {max_pages}）；"
        "已停止生成，未裁切正文或覆盖上一版"
    )


# ---------------------------------------------------------------- 主流程
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(ROOT / "aibrief"))
    parser.add_argument("--demo", action="store_true", help="仅用内置演示数据（本地预览）")
    parser.add_argument("--require-live", action="store_true", help="实时源不足时退出 3")
    parser.add_argument("--require-fresh-market", action="store_true",
                        help="A 股不是最新交易日或回退快照时退出 3")
    parser.add_argument("--min-live-sources", type=int, default=20)
    parser.add_argument("--limit", type=int, default=None,
                        help="每个来源抓取条数（默认取 002 推送口径：会员 20 条）")
    parser.add_argument("--pages", type=int, default=0, help="最大页数（0=固件上限 48）")
    parser.add_argument("--news-per-source", type=int, default=NEWS_PER_SOURCE_FULL,
                        help="快讯每源条数；0=全量推送（默认，放不下时自动收敛）")
    args = parser.parse_args()
    if args.pages < 0:
        parser.error("--pages 不能为负数")
    if args.news_per_source < 0:
        parser.error("--news-per-source 不能为负数")
    if args.limit is None:
        args.limit = sources.default_fetch_limit()
    if args.limit <= 0:
        parser.error("--limit 必须大于 0")

    started = time.time()
    data = collect_brief_002(limit=args.limit, demo=args.demo)
    brief = data["brief"]
    live, total = live_source_stats(brief)
    demo = live == 0
    if args.demo:
        demo = True
    print(f"采集完成：{total} 源，实时 {live} 源，每源最多 {args.limit} 条，"
          f"用时 {time.time() - started:.1f}s，字体 {FONT_PATH.name}")
    mode = "全量推送（放不下时自动收敛）" if args.news_per_source <= 0 else f"每源 {args.news_per_source} 条精选"
    print(f"快讯口径：{mode}")

    if args.require_live and live < args.min_live_sources:
        print(f"实时源不足（{live} < {args.min_live_sources}），放弃更新设备简报", file=sys.stderr)
        return 3

    freshness = data.get("market_freshness") or {}
    if args.require_fresh_market:
        print(f"大盘新鲜度检查：{freshness.get('reason') or '无检查结果'}")
        if not freshness.get("ok"):
            print("大盘数据不是最新，放弃更新设备简报并保留上一版", file=sys.stderr)
            return 3

    now = china_now()
    try:
        renderer = render_with_page_budget(
            data, now, live, total, demo, max_pages=args.pages or MAX_PAGES,
            news_per_source=args.news_per_source,
        )
    except LayoutError as exc:
        print(f"排版预检未通过：{exc}", file=sys.stderr)
        return 5

    meta = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Shanghai",
        "bias": data["summary"].get("bias"),
        "bull": data["summary"].get("bull"),
        "bear": 100 - _int(data["summary"].get("bull"), 50),
        "signals": data["summary"].get("signals"),
        "live_sources": live,
        "total_sources": total,
        "demo": demo,
        "market_date": (data.get("review_ashare") or {}).get("date"),
        "market_source": (data.get("review_ashare") or {}).get("source"),
        "market_freshness": freshness,
        "font": str(FONT_PATH.name),
        "source_repo": "002",
        "fetch_limit": args.limit,
        "news_mode": "full" if args.news_per_source <= 0 else f"per_source_{args.news_per_source}",
    }

    out_dir = Path(args.out_dir)
    try:
        renderer.save(out_dir, meta)
    except LayoutError as exc:
        print(f"排版预检未通过：{exc}", file=sys.stderr)
        return 5
    print(f"全页校样：{out_dir}/latest_contact_sheet.png")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
