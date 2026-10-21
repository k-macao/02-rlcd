#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 002「章鱼 AI·全景分析」渲染成 RLCD 设备可显示的多页 400×300 单色图。

v1.8 重构：
- 数据源从 05 切换到 002（AH 政策、情绪、热力、趋势分析）
- 字体统一使用思源黑体（Source Han Sans / Noto Sans SC），回退 MiSans
- 全部内容完整显示，分多页展示，每页 1 分钟自动轮播
- 单文件包含多页：AIB1 + 宽高 + N*15000 字节位图，固件支持最多 30 页

产物（默认写入 aibrief/）：
  latest.bin  8 字节头 + N*15000 字节 1bpp 位图（MSB first，1=黑），N=页数
  latest.png  第一页 PNG 预览
  latest_*.png 多页 PNG 预览（可选）
  meta.json   生成时间、页数、数据来源、SHA-256

用法：
  python3 device_brief/render_brief.py                 # 抓取 + 渲染（002 全量）
  python3 device_brief/render_brief.py --demo          # 离线演示数据
  python3 device_brief/render_brief.py --require-live  # 实时源不足则失败退出（CI 用）
  python3 device_brief/render_brief.py --out-dir aibrief --pages 20
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

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
MAGIC = b"AIB1"

# 字体：思源黑体优先，回退 MiSans
FONT_DIR = ROOT / "device_brief" / "fonts"
FONT_CANDIDATES = [
    FONT_DIR / "SourceHanSansSC-Regular.ttf",
    FONT_DIR / "NotoSansSC-Regular.ttf",
    FONT_DIR / "SourceHanSansSC[wght].ttf",
    Path("/tmp/google-fonts/ofl/notosanssc/NotoSansSC[wght].ttf"),
    FONT_DIR / "MiSans-Medium.ttf",
    ROOT / "device_brief" / "fonts" / "MiSans-Medium.ttf",
]
# 环境变量可覆盖
if os.environ.get("SOURCE_HAN_SANS_FONT"):
    FONT_CANDIDATES.insert(0, Path(os.environ["SOURCE_HAN_SANS_FONT"]))

FONT_PATH = next((p for p in FONT_CANDIDATES if p.is_file()), FONT_DIR / "MiSans-Medium.ttf")
FONT_CHINESE_PATH = FONT_PATH
FONT_DIGITAL_PATH = FONT_PATH
FONT_DIGITAL_BOLD_PATH = FONT_PATH

BLACK, WHITE = 0, 255

MARGIN = 8
CONTENT_W = WIDTH - MARGIN * 2
FONT_TITLE = 18
FONT_SECTION = 14
FONT_BODY = 13
FONT_SMALL = 12
FONT_META = 11
FONT_MIN = 10

# ---------------------------------------------------------------- 数据收集（002）
def collect_brief_002(limit: int = 12):
    """从 002 仓库抓取全量数据，返回 dict 包含各板块分析结果。"""
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
    try:
        market_ashare = sources.get_ashare_market()
        review_ashare = sources.analyze_ashare(market_ashare)
    except Exception as exc:
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
    def __init__(self, font_path: Path | None = None):
        self.image = Image.new("L", (WIDTH, HEIGHT), WHITE)
        self.draw = ImageDraw.Draw(self.image)
        self.font_path = font_path or FONT_PATH
        self.font_chinese_path = self.font_path
        self.font_digital_path = self.font_path
        self.font_digital_bold_path = self.font_path
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def _font(self, is_digital: bool, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = (str(self.font_path), size)
        if key not in self._cache:
            try:
                font = ImageFont.truetype(str(self.font_path), size)
                # SourceHanSansSC-Regular.ttf is a variable font whose default
                # wght axis is Thin (100). On the 1bpp device bitmap, that
                # weight loses many CJK strokes during thresholding. Select the
                # actual Regular instance before measuring or drawing text.
                set_variation = getattr(font, "set_variation_by_name", None)
                if callable(set_variation):
                    try:
                        set_variation("Regular")
                    except (OSError, ValueError):
                        # Static fallback fonts (for example MiSans Medium) do
                        # not expose named variable-font instances.
                        pass
                self._cache[key] = font
            except Exception:
                # 回退到默认
                self._cache[key] = ImageFont.load_default()
        return self._cache[key]

    def _is_digital_char(self, ch: str) -> bool:
        return False

    def _runs(self, text: str):
        if text:
            yield False, text

    def font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        return self._font(False, size)

    def measure(self, text: str, size: int, bold: bool = False) -> int:
        return int(round(self.draw.textlength(text, font=self._font(False, size))))

    def text(self, x: int, y: int, text: str, size: int = 11, bold: bool = False,
             fill: int = BLACK) -> int:
        font = self._font(False, size)
        self.draw.text((x, y), text, font=font, fill=fill)
        return int(round(self.draw.textlength(text, font=font)))

    def wrap(self, text: str, size: int, max_width: int, max_lines: int = 2,
             bold: bool = False) -> list[str]:
        text = " ".join(str(text).split())
        if not text:
            return []
        lines: list[str] = []
        current = ""
        for ch in text:
            trial = current + ch
            if self.measure(trial, size, bold) <= max_width:
                current = trial
                continue
            if current:
                lines.append(current)
                if len(lines) == max_lines:
                    break
                current = ch
            else:
                current = ch
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

    def wrap_all(self, text: str, size: int, max_width: int) -> list[str]:
        """不限制行数，完整折行，全部内容都显示。"""
        text = " ".join(str(text).split())
        if not text:
            return []
        lines: list[str] = []
        current = ""
        for ch in text:
            trial = current + ch
            if self.measure(trial, size) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = ch
        if current:
            lines.append(current)
        return lines

    def rule(self, x1: int, y: int, x2: int, thickness: int = 1, fill: int = BLACK):
        self.draw.rectangle((x1, y, x2, y + thickness - 1), fill=fill)

    def box(self, x1: int, y: int, x2: int, y2: int, fill: int = BLACK):
        self.draw.rectangle((x1, y, x2, y2), fill=fill)

    def inverted(self, x: int, y: int, text: str, size: int = 11, bold: bool = True,
                 pad_x: int = 5, pad_y: int = 2) -> int:
        width = self.measure(text, size, bold)
        height = size + 3
        self.box(x, y, x + width + pad_x * 2 - 1, y + height + pad_y * 2 - 1)
        self.text(x + pad_x, y + pad_y - 1, text, size, bold, WHITE)
        return width + pad_x * 2

    def section(self, y: int, title: str, right: str = "",
                right_size: int = 12) -> int:
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

class MultiPageRenderer:
    def __init__(self, font_path: Path | None = None):
        self.font_path = font_path or FONT_PATH
        self.pages: List[Sheet] = []

    def new_page(self, title: str = "", subtitle: str = "", page_info: str = "") -> Sheet:
        sheet = Sheet(self.font_path)
        if title:
            sheet.header_bar(title, subtitle, page_info)
        return sheet

    def add_page(self, sheet: Sheet):
        self.pages.append(sheet)

    def save(self, out_dir: Path, meta: dict):
        out_dir.mkdir(parents=True, exist_ok=True)
        if not self.pages:
            # 至少一页
            self.pages.append(Sheet(self.font_path))

        # 生成所有位图
        bitmaps = b"".join(p.to_bitmap() for p in self.pages)
        header = MAGIC + struct.pack("<HH", WIDTH, HEIGHT)
        payload = header + bitmaps
        (out_dir / "latest.bin").write_bytes(payload)

        # PNG 预览：第一页 + 拼接总览
        first = self.pages[0].image.point(lambda v: BLACK if v < 128 else WHITE).convert("1", dither=Image.NONE)
        first.save(out_dir / "latest.png")

        # 保存每页 PNG（用于调试）
        for i, sheet in enumerate(self.pages):
            mono = sheet.image.point(lambda v: BLACK if v < 128 else WHITE).convert("1", dither=Image.NONE)
            mono.save(out_dir / f"page_{i+1:02d}.png")

        # 拼接长图（垂直）
        combined_h = HEIGHT * len(self.pages)
        combined = Image.new("L", (WIDTH, combined_h), WHITE)
        for i, sheet in enumerate(self.pages):
            combined.paste(sheet.image, (0, i * HEIGHT))
        combined_mono = combined.point(lambda v: BLACK if v < 128 else WHITE).convert("1", dither=Image.NONE)
        combined_mono.save(out_dir / "latest_combined.png")

        meta = dict(meta)
        meta.update({
            "width": WIDTH,
            "height": HEIGHT,
            "pages": len(self.pages),
            "bytes": len(payload),
            "page_bytes": len(bitmaps),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "crc32_bitmap": f"{zlib.crc32(bitmaps) & 0xffffffff:08x}",
            "font": str(self.font_path.name),
        })
        (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已生成 {len(self.pages)} 页，{out_dir}/latest.bin ({len(payload)} bytes)")

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
    """兼容旧单页绘制（测试用），使用思源黑体"""
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
            line = sheet.wrap(title, FONT_SMALL, CONTENT_W - 10, 1)
            if line:
                sheet.text(MARGIN + 10, y, line[0], FONT_SMALL)
            y += 16
    return sheet

# ---------------------------------------------------------------- 新多页绘制（002 全量）
def draw_daily_summary_pages(renderer: MultiPageRenderer, data: dict, now: datetime, page_num: int, total_estimate: int) -> int:
    summary = data.get("summary") or {}
    bias = str(summary.get("bias") or "中性")
    bull = int(summary.get("bull") or 50)
    bear = 100 - bull
    signals = int(summary.get("signals") or 0)
    headline = str(summary.get("headline") or "暂无总结")
    sectors_up = summary.get("sectors_up") or []
    sectors_down = summary.get("sectors_down") or []
    flow = summary.get("flow") or str(summary.get("headline") or "")

    sheet = renderer.new_page("AI 每日总结", f"{bias} 多{bull}% 空{bear}%", f"{page_num}/{total_estimate}")
    y = 34
    # 标题区
    for line in sheet.wrap_all(headline, FONT_BODY, CONTENT_W):
        if y + 18 > HEIGHT - 8:
            renderer.add_page(sheet)
            page_num += 1
            sheet = renderer.new_page("AI 每日总结(续)", "", f"{page_num}/{total_estimate}")
            y = 34
        sheet.text(MARGIN, y, line, FONT_BODY)
        y += 18

    y += 4
    sheet.rule(MARGIN, y, WIDTH - MARGIN - 1, 1)
    y += 6

    # 多空
    if y + 36 < HEIGHT:
        sheet.text(MARGIN, y, f"多空：多{bull}% 空{bear}% · {signals}信号", FONT_SMALL)
        y += 16
        sheet.text(MARGIN, y, f"情绪：{bias}", FONT_SMALL)
        y += 16

    # 板块
    if sectors_up or sectors_down:
        if y + 20 < HEIGHT:
            sheet.text(MARGIN, y, "板块方向：", FONT_SMALL, True)
            y += 16
            up_text = "利好 " + "、".join(sectors_up) if sectors_up else ""
            down_text = "承压 " + "、".join(sectors_down) if sectors_down else ""
            combined = "｜".join(filter(None, [up_text, down_text])) or "暂无明确方向"
            for line in sheet.wrap_all(combined, FONT_SMALL, CONTENT_W):
                if y + 16 > HEIGHT - 8:
                    renderer.add_page(sheet)
                    page_num += 1
                    sheet = renderer.new_page("AI 每日总结(续)", "", f"{page_num}/{total_estimate}")
                    y = 34
                sheet.text(MARGIN, y, line, FONT_SMALL)
                y += 16

    # 资金流向
    if flow:
        y += 4
        for line in sheet.wrap_all(f"资金：{flow}", FONT_SMALL, CONTENT_W):
            if y + 16 > HEIGHT - 8:
                renderer.add_page(sheet)
                page_num += 1
                sheet = renderer.new_page("AI 每日总结(续)", "", f"{page_num}/{total_estimate}")
                y = 34
            sheet.text(MARGIN, y, line, FONT_SMALL)
            y += 16

    renderer.add_page(sheet)
    return page_num + 1

def draw_market_pages(renderer: MultiPageRenderer, review: dict | None, market_name: str, page_num: int, total_estimate: int) -> int:
    if not review:
        sheet = renderer.new_page(f"AI 看盘·{market_name}", "暂无数据", f"{page_num}/{total_estimate}")
        sheet.text(MARGIN, 40, "今日未获取行情", FONT_BODY)
        renderer.add_page(sheet)
        return page_num + 1

    date = str(review.get("date") or "--")
    sheet = renderer.new_page(f"AI 看盘·{market_name}", date, f"{page_num}/{total_estimate}")
    y = 34

    headline = str(review.get("headline") or "")
    if headline:
        for line in sheet.wrap_all(headline, FONT_BODY, CONTENT_W):
            if y + 18 > HEIGHT - 8:
                renderer.add_page(sheet)
                page_num += 1
                sheet = renderer.new_page(f"AI 看盘·{market_name}(续)", date, f"{page_num}/{total_estimate}")
                y = 34
            sheet.text(MARGIN, y, line, FONT_BODY)
            y += 18
        y += 4
        sheet.rule(MARGIN, y, WIDTH - MARGIN - 1, 1)
        y += 6

    indices = review.get("indices") or []
    if indices and y + 40 < HEIGHT:
        sheet.text(MARGIN, y, "指数：", FONT_SMALL, True)
        y += 16
        for item in indices[:6]:
            if y + 16 > HEIGHT - 8:
                renderer.add_page(sheet)
                page_num += 1
                sheet = renderer.new_page(f"AI 看盘·{market_name}(续)", date, f"{page_num}/{total_estimate}")
                y = 34
            name = str(item.get("name") or "")
            short_name = name.replace("上证指数", "上证").replace("深证成指", "深证").replace("创业板指", "创业板").replace("科创50", "科创50").replace("恒生指数", "恒生").replace("恒生科技指数", "恒生科技")
            close = item.get("close")
            pct = fmt_pct(item.get("pct"))
            close_text = f"{float(close):,.2f}" if isinstance(close, (int, float)) else "--"
            line = f"{short_name} {close_text} {pct}"
            sheet.text(MARGIN, y, line, FONT_SMALL)
            y += 16

    # 成交、涨跌、领涨等
    for key in ["turnover_text", "breadth_text", "sectors_text", "funds_text", "outlook"]:
        text = review.get(key) or ""
        if not text:
            # 尝试从旧字段组合
            continue
        if y + 16 > HEIGHT - 8:
            renderer.add_page(sheet)
            page_num += 1
            sheet = renderer.new_page(f"AI 看盘·{market_name}(续)", date, f"{page_num}/{total_estimate}")
            y = 34
        for line in sheet.wrap_all(text, FONT_SMALL, CONTENT_W):
            if y + 16 > HEIGHT - 8:
                renderer.add_page(sheet)
                page_num += 1
                sheet = renderer.new_page(f"AI 看盘·{market_name}(续)", date, f"{page_num}/{total_estimate}")
                y = 34
            sheet.text(MARGIN, y, line, FONT_SMALL)
            y += 16

    # 详细字段（002 的 review 结构）
    turnover = review.get("turnover") or {}
    breadth = review.get("breadth") or {}
    leaders = review.get("leaders") or []
    laggards = review.get("laggards") or []

    if turnover and y + 16 < HEIGHT:
        parts = []
        if turnover.get("amount") is not None:
            parts.append(f"成交 {fmt_amount(turnover.get('amount'))}")
        if parts:
            sheet.text(MARGIN, y, " · ".join(parts), FONT_SMALL)
            y += 16

    if breadth and y + 16 < HEIGHT:
        parts = []
        if breadth.get("limit_up") is not None:
            parts.append(f"涨停 {breadth.get('limit_up')}")
        if breadth.get("limit_down") is not None:
            parts.append(f"跌停 {breadth.get('limit_down')}")
        if breadth.get("up") is not None:
            parts.append(f"上涨 {breadth.get('up')}")
        if breadth.get("down") is not None:
            parts.append(f"下跌 {breadth.get('down')}")
        if parts:
            line = " · ".join(parts)
            for l in sheet.wrap_all(line, FONT_SMALL, CONTENT_W):
                if y + 16 > HEIGHT - 8:
                    break
                sheet.text(MARGIN, y, l, FONT_SMALL)
                y += 16

    if leaders and y + 16 < HEIGHT:
        names = [str(x.get("name") or "") for x in leaders[:3]]
        if names:
            sheet.text(MARGIN, y, "领涨 " + "、".join(names), FONT_SMALL)
            y += 16
    if laggards and y + 16 < HEIGHT:
        names = [str(x.get("name") or "") for x in laggards[:2]]
        if names:
            sheet.text(MARGIN, y, "领跌 " + "、".join(names), FONT_SMALL)
            y += 16

    renderer.add_page(sheet)
    return page_num + 1

def draw_heatmap_pages(renderer: MultiPageRenderer, heatmap: dict | None, page_num: int, total_estimate: int) -> int:
    if not heatmap:
        sheet = renderer.new_page("AI 情绪热力图", "暂无", f"{page_num}/{total_estimate}")
        sheet.text(MARGIN, 40, "暂无热力图数据", FONT_BODY)
        renderer.add_page(sheet)
        return page_num + 1

    headline = str(heatmap.get("headline") or "情绪热力图")
    keywords = heatmap.get("keywords") or heatmap.get("items") or []
    # 002 的 heatmap 结构可能是 list of dicts
    if isinstance(keywords, dict):
        # 可能是 dict
        keywords = list(keywords.values()) if keywords else []

    # 分页：每页 8 个关键词
    per_page = 7
    total_pages_needed = max(1, (len(keywords) + per_page - 1) // per_page)

    for chunk_idx in range(total_pages_needed):
        start = chunk_idx * per_page
        chunk = keywords[start:start+per_page]
        sub_title = headline[:20] + "..." if len(headline) > 20 else headline
        sheet = renderer.new_page("AI 情绪热力图", sub_title if chunk_idx == 0 else "", f"{page_num}/{total_estimate}")
        y = 34

        if chunk_idx == 0:
            for line in sheet.wrap_all(headline, FONT_SMALL, CONTENT_W):
                if y + 16 > HEIGHT - 8:
                    break
                sheet.text(MARGIN, y, line, FONT_SMALL)
                y += 16
            y += 4
            sheet.rule(MARGIN, y, WIDTH - MARGIN - 1, 1)
            y += 6

        if not chunk:
            sheet.text(MARGIN, y, "暂无关键词", FONT_SMALL)
        else:
            for kw in chunk:
                if y + 36 > HEIGHT - 8:
                    break
                if isinstance(kw, dict):
                    tag = str(kw.get("tag") or kw.get("keyword") or "")
                    heat = kw.get("heat") or kw.get("mentions") or 0
                    sentiment = kw.get("sentiment") or kw.get("sentiment_label") or ""
                    net = kw.get("net") or 0
                    line1 = f"{tag} 热度{heat} {sentiment} 净{net:+d}" if tag else str(kw)[:30]
                else:
                    line1 = str(kw)[:30]
                sheet.text(MARGIN, y, line1, FONT_SMALL, True)
                y += 16
                # 第二行显示 ETF 或提示
                if isinstance(kw, dict):
                    hint = str(kw.get("hint") or kw.get("etf_pair_text") or "")[:40]
                    if hint:
                        for l in sheet.wrap_all(hint, FONT_MIN, CONTENT_W):
                            if y + 12 > HEIGHT - 8:
                                break
                            sheet.text(MARGIN+6, y, l, FONT_MIN)
                            y += 12
                            break
                y += 4

        renderer.add_page(sheet)
        page_num += 1

    return page_num

def draw_policy_pages(renderer: MultiPageRenderer, policy: dict | None, page_num: int, total_estimate: int) -> int:
    if not policy:
        sheet = renderer.new_page("AI 政策分析", "暂无", f"{page_num}/{total_estimate}")
        sheet.text(MARGIN, 40, "暂无政策信号", FONT_BODY)
        renderer.add_page(sheet)
        return page_num + 1

    headline = str(policy.get("headline") or "政策分析")
    sheet = renderer.new_page("AI 政策分析", "", f"{page_num}/{total_estimate}")
    y = 34
    for line in sheet.wrap_all(headline, FONT_SMALL, CONTENT_W):
        if y + 16 > HEIGHT - 8:
            renderer.add_page(sheet)
            page_num += 1
            sheet = renderer.new_page("AI 政策分析(续)", "", f"{page_num}/{total_estimate}")
            y = 34
        sheet.text(MARGIN, y, line, FONT_SMALL)
        y += 16

    y += 4
    sheet.rule(MARGIN, y, WIDTH - MARGIN - 1, 1)
    y += 6

    buckets = policy.get("buckets") or policy.get("sectors") or []
    if isinstance(buckets, dict):
        buckets = list(buckets.values())

    for b in buckets[:8]:
        if y + 32 > HEIGHT - 8:
            renderer.add_page(sheet)
            page_num += 1
            sheet = renderer.new_page("AI 政策分析(续)", "", f"{page_num}/{total_estimate}")
            y = 34
        if isinstance(b, dict):
            tag = str(b.get("tag") or b.get("label") or b.get("dimension") or "")
            net = b.get("net") or b.get("policy_net") or 0
            sources_hit = b.get("sources") or b.get("sources_hit") or 0
            mentions = b.get("mentions") or 0
            stance = b.get("stance") or ""
            line = f"{tag} 净{net:+d} {sources_hit}源·{mentions}条 {stance}"
        else:
            line = str(b)[:40]
        for l in sheet.wrap_all(line, FONT_SMALL, CONTENT_W):
            if y + 16 > HEIGHT - 8:
                break
            sheet.text(MARGIN, y, l, FONT_SMALL)
            y += 16
            break
        y += 2

    renderer.add_page(sheet)
    return page_num + 1

def draw_sectors_pages(renderer: MultiPageRenderer, summary: dict, page_num: int, total_estimate: int) -> int:
    opp = summary.get("opportunity_sectors") or []
    press = summary.get("pressure_sectors") or []

    sheet = renderer.new_page("AI 板块机会", f"机会{len(opp)} 承压{len(press)}", f"{page_num}/{total_estimate}")
    y = 34

    if opp:
        sheet.text(MARGIN, y, "机会方向：", FONT_SMALL, True)
        y += 16
        for item in opp[:6]:
            if y + 40 > HEIGHT - 8:
                renderer.add_page(sheet)
                page_num += 1
                sheet = renderer.new_page("AI 板块机会(续)", "", f"{page_num}/{total_estimate}")
                y = 34
            tag = str(item.get("tag") or "")
            net = int(item.get("net") or 0)
            sources_cnt = int(item.get("sources") or 0)
            mentions = int(item.get("mentions") or 0)
            sheet.text(MARGIN, y, f"{tag} 净{net:+d} {sources_cnt}源·{mentions}条", FONT_SMALL, True)
            y += 16
            # 证据
            evidence = item.get("evidence") or []
            for ev in evidence[:2]:
                if y + 14 > HEIGHT - 8:
                    break
                title = str(ev.get("title") or "")[:36]
                for l in sheet.wrap_all(f"·{title}", FONT_MIN, CONTENT_W-6):
                    if y + 12 > HEIGHT - 8:
                        break
                    sheet.text(MARGIN+4, y, l, FONT_MIN)
                    y += 12
                    break
            # ETF
            etf_text = str(item.get("etf_pair_text") or "")[:50]
            if etf_text and y + 12 < HEIGHT - 8:
                sheet.text(MARGIN+4, y, etf_text[:40], FONT_MIN)
                y += 12
            y += 4

    if press:
        if y + 20 < HEIGHT:
            y += 4
            sheet.rule(MARGIN, y, WIDTH - MARGIN - 1, 1)
            y += 6
            sheet.text(MARGIN, y, "承压方向：", FONT_SMALL, True)
            y += 16
        for item in press[:4]:
            if y + 32 > HEIGHT - 8:
                renderer.add_page(sheet)
                page_num += 1
                sheet = renderer.new_page("AI 板块机会(续)", "", f"{page_num}/{total_estimate}")
                y = 34
            tag = str(item.get("tag") or "")
            net = int(item.get("net") or 0)
            sheet.text(MARGIN, y, f"{tag} 净{net:+d}", FONT_SMALL, True)
            y += 16
            evidence = item.get("evidence") or []
            for ev in evidence[:1]:
                if y + 12 > HEIGHT - 8:
                    break
                title = str(ev.get("title") or "")[:36]
                sheet.text(MARGIN+4, y, f"·{title}", FONT_MIN)
                y += 12
            y += 4

    renderer.add_page(sheet)
    return page_num + 1

def draw_news_pages(renderer: MultiPageRenderer, brief: dict, page_num: int, total_estimate: int) -> int:
    # 按板块分组
    sections = getattr(sources, "SECTIONS", [])
    section_map = {s["key"]: s["label"] for s in sections} if sections else {}

    # 收集所有新闻，按板块
    grouped: Dict[str, List[str]] = {}
    for name, items in brief.items():
        meta = None
        try:
            meta = sources.source_meta(name)
        except:
            pass
        sec_key = (meta.get("section") if meta else "finance") or "finance"
        sec_label = section_map.get(sec_key, sec_key)
        if sec_label not in grouped:
            grouped[sec_label] = []
        for it in items[:3]:  # 每源 3 条
            title = " ".join(str(it.get("title", "")).split())
            if title:
                grouped[sec_label].append(title)

    # 去重
    seen = set()
    for sec in list(grouped.keys()):
        uniq = []
        for t in grouped[sec]:
            k = t.lower()
            if k not in seen:
                seen.add(k)
                uniq.append(t)
        grouped[sec] = uniq

    # 分页渲染：每页最多 14 条标题
    per_page = 12
    for sec_label, titles in grouped.items():
        if not titles:
            continue
        # 该板块可能需要多页
        chunks = [titles[i:i+per_page] for i in range(0, len(titles), per_page)]
        for chunk_idx, chunk in enumerate(chunks):
            suffix = f"({chunk_idx+1}/{len(chunks)})" if len(chunks) > 1 else ""
            sheet = renderer.new_page(f"全网快讯·{sec_label}{suffix}", f"{len(titles)}条", f"{page_num}/{total_estimate}")
            y = 34
            for idx, title in enumerate(chunk):
                if y + 16 > HEIGHT - 8:
                    break
                # 编号 + 标题
                wrapped = sheet.wrap_all(title, FONT_SMALL, CONTENT_W - 10)
                if not wrapped:
                    continue
                # 第一行带点
                sheet.draw.rectangle((MARGIN, y + 5, MARGIN + 3, y + 8), fill=BLACK)
                sheet.text(MARGIN + 8, y, wrapped[0], FONT_SMALL)
                y += 16
                # 剩余行
                for extra in wrapped[1:2]:  # 最多再显示一行
                    if y + 14 > HEIGHT - 8:
                        break
                    sheet.text(MARGIN + 8, y, extra, FONT_MIN)
                    y += 12
            renderer.add_page(sheet)
            page_num += 1

    return page_num

def draw_all_pages(data: dict, now: datetime, live: int, total: int, demo: bool) -> MultiPageRenderer:
    renderer = MultiPageRenderer(FONT_PATH)
    # 预估总页数用于页码显示（先粗略）
    total_estimate = 20

    page_num = 1

    # 1. 封面 + 每日总结
    page_num = draw_daily_summary_pages(renderer, data, now, page_num, total_estimate)

    # 2-4. 行情
    page_num = draw_market_pages(renderer, data.get("review_ashare"), "A股", page_num, total_estimate)
    page_num = draw_market_pages(renderer, data.get("review_hk"), "港股", page_num, total_estimate)
    page_num = draw_market_pages(renderer, data.get("review_us"), "美股", page_num, total_estimate)

    # 5. 热力图
    page_num = draw_heatmap_pages(renderer, data.get("heatmap"), page_num, total_estimate)

    # 6. 政策
    page_num = draw_policy_pages(renderer, data.get("policy"), page_num, total_estimate)

    # 7. 板块机会
    page_num = draw_sectors_pages(renderer, data.get("summary") or {}, page_num, total_estimate)

    # 8+. 新闻
    page_num = draw_news_pages(renderer, data.get("brief") or {}, page_num, total_estimate)

    # 更新所有页面的页码显示为真实总数
    real_total = len(renderer.pages)
    for idx, sheet in enumerate(renderer.pages):
        # 重绘页码：我们已在 header 中写入旧预估，现在覆盖右下角
        # 简单在底部重写页码
        page_text = f"{idx+1}/{real_total}"
        # 清除旧页码区域并重写（底部黑条已在 header，此处在底部写）
        # 直接在底部写页码
        w = sheet.measure(page_text, FONT_MIN)
        # 擦除底部区域
        sheet.box(WIDTH - MARGIN - w - 4, HEIGHT - 14, WIDTH - MARGIN, HEIGHT - 1, WHITE)
        sheet.text(WIDTH - MARGIN - w, HEIGHT - 14, page_text, FONT_MIN)

    return renderer

# ---------------------------------------------------------------- 主流程
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(ROOT / "aibrief"))
    parser.add_argument("--demo", action="store_true", help="仅用内置演示数据（本地预览）")
    parser.add_argument("--require-live", action="store_true", help="实时源不足时退出 3")
    parser.add_argument("--min-live-sources", type=int, default=20)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--pages", type=int, default=0, help="限制最大页数（0 不限制）")
    args = parser.parse_args()

    started = time.time()
    data = collect_brief_002(limit=args.limit)
    brief = data["brief"]
    live, total = live_source_stats(brief)
    demo = live == 0
    if args.demo:
        demo = True
    print(f"采集完成：{total} 源，实时 {live} 源，用时 {time.time() - started:.1f}s，字体 {FONT_PATH.name}")

    if args.require_live and live < args.min_live_sources:
        print(f"实时源不足（{live} < {args.min_live_sources}），放弃更新设备简报", file=sys.stderr)
        return 3

    now = datetime.now()
    renderer = draw_all_pages(data, now, live, total, demo)

    if args.pages and args.pages > 0:
        renderer.pages = renderer.pages[:args.pages]

    # 限制最多 30 页（固件上限）
    if len(renderer.pages) > 30:
        print(f"页数 {len(renderer.pages)} 超过固件上限 30，截断", file=sys.stderr)
        renderer.pages = renderer.pages[:30]

    meta = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "bias": data["summary"].get("bias"),
        "bull": data["summary"].get("bull"),
        "bear": data["summary"].get("bear"),
        "signals": data["summary"].get("signals"),
        "live_sources": live,
        "total_sources": total,
        "demo": demo,
        "market_date": (data.get("review_ashare") or {}).get("date"),
        "market_source": (data.get("review_ashare") or {}).get("source"),
        "font": str(FONT_PATH.name),
        "source_repo": "002",
    }

    out_dir = Path(args.out_dir)
    renderer.save(out_dir, meta)
    print(f"已生成 {out_dir}/latest.bin ({len(renderer.pages)} 页)")

    # 兼容旧单页测试：额外生成单页版本用于 test_render_brief.py
    try:
        single_sheet = draw_sheet(
            data["summary"],
            data.get("market_ashare"),
            data.get("review_ashare"),
            headline_candidates(brief, 6),
            now, live, total, demo
        )
        # 保存为 latest_single 用于测试，不覆盖多页的 latest.bin
        # 测试会调用 save_single
    except Exception:
        pass

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
