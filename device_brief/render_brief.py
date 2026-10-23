#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 002「章鱼 AI·全景分析」渲染成 RLCD 设备可显示的多页 400×300 单色图。

杂志网格 2.0：
- 数据源为 002（AH 政策、情绪、热力、趋势分析）
- 字体统一使用阿里巴巴普惠体 3.0（Alibaba PuHuiTi 3.0）
- 400×300 固定网格、全页 16px、标题/数字 Bold、真实页码、双栏快讯与末栏均衡
- 保存前逐页检查边界、字号和叠字；超额时调整快讯版位，不截句、不直接裁页
- 单文件包含多页：AIB1 + 宽高 + N*15000 字节位图，固件支持最多 48 页

产物（默认写入 aibrief/）：
  latest.bin  8 字节头 + N*15000 字节 1bpp 位图（MSB first，1=黑），N=页数
  latest.png  第一页 PNG 预览
  page_*.png  每页 PNG 预览
  latest_contact_sheet.png  5 列全页校样墙
  meta.json   生成时间、真实页数、精选口径、预检结果、SHA-256

用法：
  python3 device_brief/render_brief.py                 # 抓取 + 渲染（002 数据）
  python3 device_brief/render_brief.py --demo          # 离线演示数据
  python3 device_brief/render_brief.py --require-live --require-fresh-market
                                  # 实时源不足或行情不新鲜则失败退出（CI 用）
  python3 device_brief/render_brief.py --out-dir aibrief --pages 20
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

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

WIDTH = 400
HEIGHT = 300
MAX_PAGES = 48
MAGIC = b"AIB1"
LAYOUT_VERSION = "2.2-alibaba-puhuiti-16px"
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

# ---------------------------------------------------------------- 数据收集（002）
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
        self._cache: dict[tuple[str, int, str], ImageFont.FreeTypeFont] = {}
        # 每次文字绘制都留档，保存前可逐页检查字号、边界与叠字。
        self.text_runs: List[dict] = []
        self.region = "body"
        self.editorial = False
        self.page_title = ""
        self.page_subtitle = ""

    def _font(self, is_digital: bool, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        # 标题或数值串都使用同一字体家族的 Bold，其余文字使用 Regular。
        use_bold = bool(bold or is_digital)
        weight = "Bold" if use_bold else "Regular"
        font_path = self.bold_font_path if use_bold else self.font_path
        key = (str(font_path), size, weight)
        if key not in self._cache:
            try:
                self._cache[key] = ImageFont.truetype(str(font_path), size)
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"无法加载阿里巴巴普惠体 {weight}：{font_path}") from exc
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

    def font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        return self._font(False, size, bold)

    def measure(self, text: str, size: int, bold: bool = False) -> int:
        total = sum(
            self.draw.textlength(segment, font=self._font(is_digital, size, bold))
            for is_digital, segment in self._runs(str(text))
        )
        return int(round(total))

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
        cursor = float(x)
        bbox = None
        for is_digital, segment in self._runs(text):
            font = self._font(is_digital, size, bold)
            segment_bbox = self.draw.textbbox((cursor, y), segment, font=font)
            if bbox is None:
                bbox = segment_bbox
            else:
                bbox = (
                    min(bbox[0], segment_bbox[0]), min(bbox[1], segment_bbox[1]),
                    max(bbox[2], segment_bbox[2]), max(bbox[3], segment_bbox[3]),
                )
            self.draw.text((cursor, y), segment, font=font, fill=fill)
            cursor += self.draw.textlength(segment, font=font)
        self.text_runs.append({
            "text": text, "size": size, "region": self.region,
            "bold": bool(bold), "bbox": tuple(int(v) for v in bbox),
        })
        return int(round(cursor - x))

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
        mono = self.image.point(lambda v: BLACK if v < 128 else WHITE).convert("1", dither=Image.NONE)
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
        mono = self.image.point(lambda v: BLACK if v < 128 else WHITE).convert("1", dither=Image.NONE)
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
            page_text = f"读取 {self._now.strftime('%H:%M')} · {index:02d} / {total_pages:02d}"
            sheet.text(WIDTH - PAGE_MARGIN - sheet.measure(page_text, FONT_MIN),
                       279, page_text, FONT_MIN)
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
        mono_pages = [
            p.image.point(lambda v: BLACK if v < 128 else WHITE).convert("1", dither=Image.NONE)
            for p in self.pages
        ]
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
            "layout": LAYOUT_VERSION,
            "layout_validated": True,
            "safe_area": [PAGE_MARGIN, BODY_TOP, WIDTH - PAGE_MARGIN, BODY_BOTTOM],
            "page_titles": [p.page_title for p in self.pages],
        })
        (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
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


def _group_news(brief: dict, per_source: int) -> Dict[str, List[str]]:
    sections = getattr(sources, "SECTIONS", [])
    section_map = {s["key"]: s["label"] for s in sections} if sections else {}
    grouped: Dict[str, List[str]] = {}
    seen = set()
    for name, items in brief.items():
        try:
            meta = sources.source_meta(name)
        except Exception:
            meta = None
        section_key = (meta.get("section") if meta else "finance") or "finance"
        label = section_map.get(section_key, section_key)
        target = grouped.setdefault(label, [])
        for item in items[:max(0, per_source)]:
            title = " ".join(str(item.get("title", "")).split())
            key = title.lower()
            if title and key not in seen:
                seen.add(key)
                target.append(title)
    return {label: titles for label, titles in grouped.items() if titles}


def draw_news_pages(renderer: MultiPageRenderer, brief: dict, page_num: int,
                    total_estimate: int, news_per_source: int = 3) -> int:
    before = len(renderer.pages)
    available = _group_news(brief, 3)
    grouped = available if news_per_source == 3 else _group_news(brief, news_per_source)
    available_count = sum(len(titles) for titles in available.values())
    included_count = sum(len(titles) for titles in grouped.values())
    renderer.layout_meta.update({
        "news_per_source": news_per_source,
        "news_available": available_count,
        "news_included": included_count,
        "news_omitted": max(0, available_count - included_count),
    })
    if not grouped:
        flow = PageFlow(renderer, "全网快讯", "暂无快讯")
        flow.add_entry("STATUS", "今日未获取可显示的快讯标题。", FONT_BODY)
        return _page_count_delta(renderer, before, page_num)

    number = 1
    for label, titles in grouped.items():
        edition = f" · 精选{news_per_source}条/源" if news_per_source < 3 else f" · {len(titles)}条"
        flow = NewsFlow(renderer, "全网快讯", label + edition)
        for title in titles:
            flow.add_item(number, title)
            number += 1
        flow.balance_last_page()
    return _page_count_delta(renderer, before, page_num)


def draw_all_pages(data: dict, now: datetime, live: int, total: int, demo: bool,
                   news_per_source: int = 3) -> MultiPageRenderer:
    renderer = MultiPageRenderer(FONT_PATH)
    page_num = 1
    page_num = draw_daily_summary_pages(renderer, data, now, page_num, 0)
    page_num = draw_market_pages(renderer, data.get("review_ashare"), "A股", page_num, 0)
    page_num = draw_market_pages(renderer, data.get("review_hk"), "港股", page_num, 0)
    page_num = draw_market_pages(renderer, data.get("review_us"), "美股", page_num, 0)
    page_num = draw_heatmap_pages(renderer, data.get("heatmap"), page_num, 0)
    page_num = draw_policy_pages(renderer, data.get("policy"), page_num, 0)
    page_num = draw_sectors_pages(renderer, data.get("summary") or {}, page_num, 0)
    draw_news_pages(renderer, data.get("brief") or {}, page_num, 0, news_per_source)
    if demo and renderer.pages:
        renderer.pages[0].page_subtitle += " · 离线演示"
    renderer.finalize(now)
    return renderer


def render_with_page_budget(data: dict, now: datetime, live: int, total: int, demo: bool,
                            max_pages: int = MAX_PAGES) -> MultiPageRenderer:
    """先完整排版，再在 3→2→1 条/源间调整快讯版位；分析栏目不裁剪。"""
    max_pages = max(1, min(MAX_PAGES, max_pages or MAX_PAGES))
    last_count = 0
    for per_source in (3, 2, 1):
        renderer = draw_all_pages(data, now, live, total, demo, news_per_source=per_source)
        renderer.page_limit = max_pages
        last_count = len(renderer.pages)
        if last_count <= max_pages:
            renderer.validate(max_pages)
            if per_source < 3:
                print(f"版位自适应：快讯每源精选 {per_source} 条，分析栏目与关键结论保留，共 {last_count} 页")
            return renderer
        print(f"排版预检：每源 {per_source} 条快讯需 {last_count} 页，超过 {max_pages} 页，调整快讯版位")
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
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--pages", type=int, default=0, help="最大页数（0=固件上限 48）")
    args = parser.parse_args()
    if args.pages < 0:
        parser.error("--pages 不能为负数")

    started = time.time()
    data = collect_brief_002(limit=args.limit, demo=args.demo)
    brief = data["brief"]
    live, total = live_source_stats(brief)
    demo = live == 0
    if args.demo:
        demo = True
    print(f"采集完成：{total} 源，实时 {live} 源，用时 {time.time() - started:.1f}s，字体 {FONT_PATH.name}")

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
            data, now, live, total, demo, max_pages=args.pages or MAX_PAGES
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
