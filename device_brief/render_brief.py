#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「全球行情数据面板」渲染成 RLCD 设备可显示的单页 400×300 单色图。

设备主页只显示最新数据：A、港、美、韩、日五个市场共 12 个指数的
最新点位、涨跌幅、成交量（股）与成交额（本币），外加沪深两市成交额。
不再有 AI 简报、快讯和政策分析页面。

两条更新链路共用同一张版面：
- 新固件（v1.8.7+）每分钟直连行情接口，在设备上按同一套坐标自绘面板；
- 本脚本由 GitHub Actions 里的常驻循环（``publish_loop.py``）每分钟出一张位图，
  供旧固件显示，并在设备直连行情接口失败时兜底。

版面坐标、固定文字和数字格式在固件里各有一份
（``RLCD_CLOCK/main/ui/ui_market_panel_layout.h``、``ui_market_panel_render.cpp``、
``RLCD_CLOCK/main/network/market_panel_contract.h``），改动必须两边同步：
``test_render_brief.py`` 会逐项比对常量，``fixtures_tool.py`` 生成的基准图
要求固件自绘结果与本脚本的位图逐像素相同。

排版规矩沿用原渲染器：阿里巴巴普惠体 3.0、全页 16px、数值串 Bold、
FreeType 原生 MONO + 整数像素定位、页面只有纯黑 / 纯白；保存前检查
画布、字号、越界与叠字，未通过时不覆盖上一版。

文件名与输出目录沿用旧名（render_brief.py / aibrief/），因为已发布固件的
下载地址写死为 ``aibrief/latest.bin`` 与 ``aibrief/version.txt``。

产物（默认写入 aibrief/）：
  latest.bin   AIB1 + 宽高 + 15000 字节 1bpp 位图（MSB first，1=黑），固定 1 页
  version.txt  设备条件请求用的小清单（sha256 / 页数 / 字节数），内容不变就不下载
  meta.json    行情时间、各数据源状态、逐行数值、预检结果、SHA-256
  latest.png   PNG 预览（``--no-preview`` 时不生成，已有的会删掉）

用法：
  python3 device_brief/render_brief.py                 # 抓取实时行情并渲染
  python3 device_brief/render_brief.py --demo          # 离线演示（fixtures 里的实测行情）
  python3 device_brief/render_brief.py --require-live  # 有效行数不足则退出 3，保留上一版（CI 用）
  python3 device_brief/render_brief.py --no-preview    # 只出设备要读的文件（每分钟发布时用）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image, ImageDraw  # noqa: E402

import market_quotes  # noqa: E402
from market_quotes import (  # noqa: E402
    MARKET_ORDER, PanelData, fmt_cn_number, fmt_pct, fmt_price,
)
from mono_text import MonoFont, get_mono_font  # noqa: E402

WIDTH = 400
HEIGHT = 300
# AIB1 负载格式允许多页（N * 15000 字节），数据面板固定只出 1 页；
# 新固件也只接受 1 页（aibrief_contract.h 的 kMaxPages）。
PANEL_PAGES = 1
MAGIC = b"AIB1"
# 设备先拉这个几十字节的小清单比对新旧 sha256，只有内容变了才下载位图。
MANIFEST_NAME = "version.txt"
PREVIEW_NAME = "latest.png"
MANIFEST_VERSION = 1
LAYOUT_VERSION = "3.0-market-panel-12rows"
CN_TIMEZONE = market_quotes.TZ_CN
# 旧版多页简报留下的产物；面板只有一页，保存时一并清掉，避免残页被当成新内容。
STALE_OUTPUTS = ("latest_combined.png", "latest_contact_sheet.png")
STALE_GLOBS = ("page_*.png",)
# 有效行数低于这个值就不更新设备图（12 行里允许个别数据源临时失败）。
DEFAULT_MIN_ROWS = 9


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
FONT_SIZE = 16

# ---------------------------------------------------------------- 版面（固件自绘共用同一套坐标）
# 所有文字 y 都是文字原点（ascender 顶），与 Sheet.text 的 y 含义一致：
# 普惠体 16px 的基线在原点下方 17px，汉字墨迹占原点下方第 4..18 行。
TEXT_BASELINE = 17
INK_TOP = 4
INK_BOTTOM = 18
HEADER_BAR_BOTTOM = 23          # 顶部黑条占 y=0..23
HEADER_TEXT_Y = 1
PAGE_LEFT = 6
PAGE_RIGHT = 394                # 文字与线条的右边界（含）
COLUMN_HEADER_Y = 24
COLUMN_RULE_Y = 45
ROW_TOP = 47
ROW_PITCH = 19
TAG_X0, TAG_X1 = 4, 23          # 市场色块（黑底白字）
TAG_PAD_TOP = INK_TOP - 1       # 色块比本组文字墨迹上方多出 1px
NAME_X = 29
PRICE_RIGHT = 181               # 各数值列右对齐的右边界（不含）
PCT_RIGHT = 246
VOLUME_RIGHT = 317
TURNOVER_RIGHT = 395
FOOTER_RULE_Y = 277
FOOTER_TEXT_Y = 279
# 页脚右侧的状态区 (x0, y0, x1, y1)，右下不含：设备在这里写「每分钟更新」或
# 「读取 HH:MM」（旧固件与位图兜底会先把它清成白底），位图里这块必须留白。
# 与固件 ui_market_panel_layout.h 的 kFooterStatusLeft / kFooterStatusRight 对应。
DEVICE_OVERLAY_BOX = (194, 279, 388, 300)

TITLE_TEXT = "全球行情"
COLUMN_TITLES = ("指数", "最新", "涨跌幅", "成交量", "成交额")
TWO_MARKET_LABEL = market_quotes.TWO_MARKET_LABEL
EMPTY_CELL = market_quotes.EMPTY_CELL

# 数字及与其组成一个数值串的符号使用 Bold；其余字符使用 Regular。
_NUMERIC_RUN = re.compile(r"[+-]?\d+(?:[.,:/-]\d+)*%?")


class Sheet:
    """一张 400×300 画布：文字只走原生 1bpp 字形，并逐次留档供保存前预检。"""

    def __init__(self, font_path: Path | None = None,
                 bold_font_path: Path | None = None):
        self.image = Image.new("L", (WIDTH, HEIGHT), WHITE)
        self.draw = ImageDraw.Draw(self.image)
        self.font_path = font_path or FONT_PATH
        self.bold_font_path = bold_font_path or FONT_BOLD_PATH
        self._cache: dict[tuple[str, int, str], MonoFont] = {}
        # 每次文字绘制都留档，保存前可检查字号、边界与叠字。
        self.text_runs: List[dict] = []
        self.region = "body"

    def _font(self, is_digital: bool, size: int, bold: bool = False) -> MonoFont:
        # 标题或数值串都使用同一字体家族的 Bold，其余文字使用 Regular。
        use_bold = bool(bold or is_digital)
        weight = "Bold" if use_bold else "Regular"
        font_path = self.bold_font_path if use_bold else self.font_path
        key = (str(font_path), size, weight)
        if key not in self._cache:
            self._cache[key] = get_mono_font(font_path, size)
        return self._cache[key]

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

    def measure(self, text: str, size: int = FONT_SIZE, bold: bool = False) -> int:
        return sum(
            self._font(is_digital, size, bold).measure(segment)
            for is_digital, segment in self._runs(str(text))
        )

    def text(self, x: int, y: int, text: str, size: int = FONT_SIZE, bold: bool = False,
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

    def text_right(self, right: int, y: int, text: str, size: int = FONT_SIZE,
                   bold: bool = False, fill: int = BLACK) -> int:
        """右对齐：文字占据 [right - 宽度, right)。"""
        width = self.measure(text, size, bold)
        self.text(right - width, y, text, size, bold, fill)
        return width

    def rule(self, x1: int, y: int, x2: int, thickness: int = 1, fill: int = BLACK):
        self.draw.rectangle((x1, y, x2, y + thickness - 1), fill=fill)

    def dotted_rule(self, x1: int, y: int, x2: int, step: int = 2, fill: int = BLACK):
        """点线：从 x1 起每隔 step 像素点一个点（固件用同样的起点和步长）。"""
        for x in range(x1, x2 + 1, step):
            self.draw.point((x, y), fill=fill)

    def box(self, x1: int, y: int, x2: int, y2: int, fill: int = BLACK):
        self.draw.rectangle((x1, y, x2, y2), fill=fill)

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


# ---------------------------------------------------------------- 设备清单
def manifest_text(sha256: str, pages: int, size: int, generated_at: str = "") -> str:
    """设备条件请求用的极简清单：一行几个 key=value，几十字节即可判断要不要下载。"""
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


class LayoutError(RuntimeError):
    """版面未通过预检时阻止写入设备负载。"""


# ---------------------------------------------------------------- 面板排版
def market_groups(panel: PanelData) -> list[tuple[str, int, int]]:
    """按显示顺序返回 (市场标签, 首行下标, 行数)。"""
    groups: list[tuple[str, int, int]] = []
    for index, row in enumerate(panel.rows):
        if groups and groups[-1][0] == row.spec.market:
            market, first, count = groups[-1]
            groups[-1] = (market, first, count + 1)
        else:
            groups.append((row.spec.market, index, 1))
    return groups


def row_y(index: int) -> int:
    return ROW_TOP + index * ROW_PITCH


def tag_geometry(first: int, count: int) -> tuple[int, int, int, int]:
    """一个市场分组的 (色块顶, 色块底, 标签文字原点 y, 组间点线 y)。

    色块上下各包住本组文字墨迹；相邻两组色块之间留 3px 白缝，点线落在白缝正中。
    """
    first_y = row_y(first)
    last_y = row_y(first + count - 1)
    block_top = first_y + TAG_PAD_TOP
    block_bottom = last_y + INK_BOTTOM
    center = (block_top + block_bottom) // 2
    label_y = center - (INK_TOP + INK_BOTTOM) // 2
    separator_y = first_y + 1
    return block_top, block_bottom, label_y, separator_y


def header_time_text(panel: PanelData) -> str:
    """右上角显示全表最新一笔行情的北京时间；休市时它不变，位图也就不变。"""
    return market_quotes.fmt_as_of(panel.as_of)


def row_cells(row) -> tuple[str, str, str, str]:
    """一行四个数值单元格的文字（固件 format_row 输出必须逐字相同）。"""
    quote = row.quote
    if not row.valid:
        return EMPTY_CELL, EMPTY_CELL, EMPTY_CELL, EMPTY_CELL
    return (
        fmt_price(quote.last),
        fmt_pct(quote.pct),
        fmt_cn_number(quote.volume),
        fmt_cn_number(quote.turnover),
    )


def footer_left_text(panel: PanelData) -> str:
    return market_quotes.two_market_text(panel.two_market_turnover())


def draw_panel(panel: PanelData, footer_right: str = "") -> Sheet:
    """画出整张数据面板。

    设备负载里右下角留白：位图兜底时固件在那里盖一层「读取 HH:MM」，位图自己
    不写渲染时间，这样行情不变时位图逐位相同。``footer_right`` 只用于生成文档
    预览图和固件自绘的回归基准图：新固件直连行情时会在同一位置写刷新状态
    （例如「每分钟更新」）。
    """
    sheet = Sheet(FONT_PATH, FONT_BOLD_PATH)

    # 顶栏：黑底白字，左标题、右行情时间。
    sheet.region = "header"
    sheet.box(0, 0, WIDTH - 1, HEADER_BAR_BOTTOM)
    title = TITLE_TEXT + (" · 离线演示" if panel.demo else "")
    sheet.text(PAGE_LEFT, HEADER_TEXT_Y, title, FONT_SIZE, True, WHITE)
    sheet.text_right(PAGE_RIGHT + 1, HEADER_TEXT_Y, header_time_text(panel),
                     FONT_SIZE, False, WHITE)

    # 列标题。
    sheet.text(NAME_X, COLUMN_HEADER_Y, COLUMN_TITLES[0])
    for right, label in zip((PRICE_RIGHT, PCT_RIGHT, VOLUME_RIGHT, TURNOVER_RIGHT),
                            COLUMN_TITLES[1:]):
        sheet.text_right(right, COLUMN_HEADER_Y, label)
    sheet.rule(TAG_X0, COLUMN_RULE_Y, PAGE_RIGHT, 1)

    # 行情行：市场色块 + 名称 + 四个右对齐数值。
    sheet.region = "body"
    for market, first, count in market_groups(panel):
        block_top, block_bottom, label_y, separator_y = tag_geometry(first, count)
        sheet.box(TAG_X0, block_top, TAG_X1, block_bottom)
        label_w = sheet.measure(market, FONT_SIZE, True)
        label_x = TAG_X0 + (TAG_X1 - TAG_X0 + 1 - label_w) // 2
        sheet.text(label_x, label_y, market, FONT_SIZE, True, WHITE)
        if first > 0:
            sheet.dotted_rule(NAME_X, separator_y, PAGE_RIGHT)
    for index, row in enumerate(panel.rows):
        y = row_y(index)
        price, pct, volume, turnover = row_cells(row)
        sheet.text(NAME_X, y, row.spec.name)
        # 「--」不含数字，显式用 Bold，和设备字库里取自 Bold 的减号保持一致。
        sheet.text_right(PRICE_RIGHT, y, price, FONT_SIZE, True)
        sheet.text_right(PCT_RIGHT, y, pct, FONT_SIZE, True)
        sheet.text_right(VOLUME_RIGHT, y, volume, FONT_SIZE, volume == EMPTY_CELL)
        sheet.text_right(TURNOVER_RIGHT, y, turnover, FONT_SIZE, turnover == EMPTY_CELL)

    # 页脚：左侧两市成交额；右侧留给固件覆盖层。
    sheet.region = "footer"
    sheet.rule(TAG_X0, FOOTER_RULE_Y, PAGE_RIGHT, 1)
    sheet.text(PAGE_LEFT, FOOTER_TEXT_Y, footer_left_text(panel))
    if footer_right:
        sheet.text_right(DEVICE_OVERLAY_BOX[2], FOOTER_TEXT_Y, footer_right)
    sheet.region = "body"
    return sheet


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def validate(sheet: Sheet) -> Sheet:
    """预检画布、字号、越界、叠字和设备覆盖区；失败时调用方不得写出任何文件。"""
    errors: List[str] = []
    if sheet.image.size != (WIDTH, HEIGHT):
        errors.append(f"画布为 {sheet.image.size}，应为 {(WIDTH, HEIGHT)}")
    if any(sheet.image.histogram()[1:255]):
        errors.append("存在灰阶像素，必须使用原生 1bpp 点阵")
    runs = [r for r in sheet.text_runs if r["text"].strip()]
    if not any(r["region"] == "body" for r in runs):
        errors.append("面板没有任何行情行")
    for run in runs:
        x0, y0, x1, y1 = run["bbox"]
        if run["size"] != FONT_SIZE:
            errors.append(f"字号 {run['size']} 不是 {FONT_SIZE}px")
        if x0 < 0 or y0 < 0 or x1 > WIDTH or y1 > HEIGHT:
            errors.append(f"文字越出画布：{run['text'][:24]!r} {run['bbox']}")
        if run["region"] == "body":
            if y0 <= COLUMN_RULE_Y or y1 > FOOTER_RULE_Y:
                errors.append(f"行情行侵入表头/页脚：{run['text'][:24]!r} {run['bbox']}")
            if _overlap(run["bbox"], DEVICE_OVERLAY_BOX):
                errors.append(f"行情行落在设备覆盖区：{run['text'][:24]!r} {run['bbox']}")
    for i, first in enumerate(runs):
        for second in runs[i + 1:]:
            if _overlap(first["bbox"], second["bbox"]):
                errors.append(f"文字叠压：{first['text'][:16]!r} / {second['text'][:16]!r}")
    if errors:
        raise LayoutError("；".join(errors[:12]))
    return sheet


def panel_meta(panel: PanelData, now: datetime) -> dict:
    as_of = panel.as_of
    rows = []
    for row in panel.rows:
        price, pct, volume, turnover = row_cells(row)
        quote = row.quote
        rows.append({
            "market": row.spec.market,
            "key": row.spec.key,
            "name": row.spec.name,
            "last": quote.last,
            "pct": quote.pct,
            "volume": quote.volume,
            "turnover": quote.turnover,
            "time": quote.time.isoformat() if quote.time else None,
            "source": quote.source,
            "cells": [price, pct, volume, turnover],
        })
    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Shanghai",
        "panel": "market",
        "markets": list(MARKET_ORDER),
        "as_of": as_of.astimezone(CN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S") if as_of else None,
        "demo": panel.demo,
        "live_rows": panel.live_rows,
        "total_rows": len(panel.rows),
        "sources": panel.sources,
        "source_labels": market_quotes.SOURCE_LABELS,
        "two_market_turnover": panel.two_market_turnover(),
        "volume_unit": "股",
        "turnover_unit": "本币",
        "rows": rows,
    }


def save(sheet: Sheet, out_dir: Path, meta: dict, preview: bool = True) -> dict:
    """先完成全部校验，再创建/覆盖交付文件；位图没变时什么都不写。

    位图里只有行情和行情时间，没有渲染时间：休市期间每次运行得到的位图逐位相同，
    这里就不重写任何文件，工作流不会产生空提交，设备也不会重复下载同一张图。

    ``preview=False`` 时不生成 PNG 预览，并删掉已有的那张（否则会留下一张与
    ``latest.bin`` 对不上的旧图）。每分钟发布时用：PNG 本身已是压缩数据，git 无法
    对它做增量压缩，实测占每次提交体积的一半以上，而设备并不读它。
    """
    validate(sheet)
    out_dir = Path(out_dir)
    bitmap = sheet.to_bitmap()
    payload = MAGIC + struct.pack("<HH", WIDTH, HEIGHT) + bitmap
    sha256 = hashlib.sha256(payload).hexdigest()

    meta = dict(meta)
    meta.update({
        "width": WIDTH,
        "height": HEIGHT,
        "pages": PANEL_PAGES,
        "bytes": len(payload),
        "page_bytes": len(bitmap),
        "sha256": sha256,
        "crc32_bitmap": f"{zlib.crc32(bitmap) & 0xffffffff:08x}",
        "font": str(sheet.font_path.name),
        "bold_font": str(sheet.bold_font_path.name),
        "font_family": "Alibaba PuHuiTi 3.0",
        "font_size": FONT_SIZE,
        "numeric_font_weight": "Bold",
        "text_font_weight": "Regular",
        "rasterizer": "FreeType FT_LOAD_TARGET_MONO + FT_LOAD_FORCE_AUTOHINT",
        "pixel_positioning": "integer",
        "gray_levels": 2,
        "layout": LAYOUT_VERSION,
        "layout_validated": True,
        "device_overlay_box": list(DEVICE_OVERLAY_BOX),
    })

    out_dir.mkdir(parents=True, exist_ok=True)
    removed = remove_stale_outputs(out_dir)
    bin_path = out_dir / "latest.bin"
    manifest_path = out_dir / MANIFEST_NAME
    preview_path = out_dir / PREVIEW_NAME
    if not preview and preview_path.is_file():
        preview_path.unlink()
        print(f"已删除 PNG 预览 {preview_path}（--no-preview）")
    unchanged = (
        bin_path.is_file() and bin_path.read_bytes() == payload
        and manifest_path.is_file()
        and parse_manifest(manifest_path.read_text(encoding="utf-8")).get("sha256") == sha256
        and (preview_path.is_file() or not preview) and (out_dir / "meta.json").is_file()
    )
    meta["unchanged"] = unchanged
    if unchanged:
        print(f"行情未变化，保留现有设备图（sha256 {sha256[:12]}…"
              f"{'，已清理旧版残页 ' + str(removed) + ' 个' if removed else ''}）")
        return meta

    bin_path.write_bytes(payload)
    if preview:
        sheet.image.convert("1", dither=Image.NONE).save(preview_path)
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_path.write_text(
        manifest_text(sha256, PANEL_PAGES, len(payload), str(meta.get("generated_at") or "")),
        encoding="utf-8")
    print(f"已预检并生成数据面板，{bin_path} ({len(payload)} bytes)")
    return meta


def remove_stale_outputs(out_dir: Path) -> int:
    """删掉旧版多页简报留下的校样图。"""
    removed = 0
    for name in STALE_OUTPUTS:
        path = out_dir / name
        if path.is_file():
            path.unlink()
            removed += 1
    for pattern in STALE_GLOBS:
        for path in out_dir.glob(pattern):
            path.unlink()
            removed += 1
    return removed


# ---------------------------------------------------------------- 主流程
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="渲染全球行情数据面板设备图")
    parser.add_argument("--out-dir", default=str(ROOT / "aibrief"))
    parser.add_argument("--demo", action="store_true",
                        help="不联网，使用 fixtures/ 里的实测行情（本地预览）")
    parser.add_argument("--require-live", action="store_true",
                        help="有效行数不足 --min-rows 时退出 3，保留上一版")
    parser.add_argument("--min-rows", type=int, default=DEFAULT_MIN_ROWS,
                        help=f"至少多少行有最新点位才更新设备图（默认 {DEFAULT_MIN_ROWS}）")
    parser.add_argument("--no-preview", action="store_true",
                        help=f"不生成 {PREVIEW_NAME} 预览并删掉已有的（每分钟发布时用，"
                             "PNG 占提交体积的一半以上而设备不读它）")
    args = parser.parse_args(argv)
    if not 1 <= args.min_rows <= len(market_quotes.PANEL_ROWS):
        parser.error(f"--min-rows 必须在 1..{len(market_quotes.PANEL_ROWS)} 之间")

    started = time.time()
    panel = market_quotes.demo_panel() if args.demo else market_quotes.collect_panel()
    sources = " ".join(
        f"{market_quotes.SOURCE_LABELS[name]}{'✓' if ok else '✗'}"
        for name, ok in panel.sources.items()
    )
    print(f"行情采集完成：{panel.live_rows}/{len(panel.rows)} 行有效，{sources}，"
          f"用时 {time.time() - started:.1f}s")
    for row in panel.rows:
        print(f"  {row.spec.market} {row.spec.name}: " + " | ".join(row_cells(row)))

    if args.require_live and (panel.demo or panel.live_rows < args.min_rows):
        print(f"有效行情不足（{panel.live_rows} < {args.min_rows}），放弃更新并保留上一版",
              file=sys.stderr)
        return 3

    try:
        sheet = draw_panel(panel)
        meta = save(sheet, Path(args.out_dir), panel_meta(panel, china_now()),
                    preview=not args.no_preview)
    except LayoutError as exc:
        print(f"排版预检未通过：{exc}", file=sys.stderr)
        return 5
    print(f"行情时间 {meta.get('as_of')} · {footer_left_text(panel)} · 版式 {LAYOUT_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
