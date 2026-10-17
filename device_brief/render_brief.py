#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 05「章鱼 AI·全景分析」渲染成 RLCD 设备可显示的 400×300 单色图。

产物（默认写入 device_brief/out/）：
  latest.bin  8 字节头 + 15000 字节 1bpp 位图（MSB first，1=黑）
  latest.png  400×300 黑白预览
  meta.json   生成时间、数据来源、实时源数量、SHA-256

用法：
  python3 device_brief/render_brief.py                 # 抓取 + 渲染
  python3 device_brief/render_brief.py --demo          # 离线演示数据（仅本地预览）
  python3 device_brief/render_brief.py --require-live  # 实时源不足则失败退出（CI 用）
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "05"))

import sources  # noqa: E402

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

WIDTH = 400
HEIGHT = 300
MAGIC = b"AIB1"
FONT_PATH = ROOT / "device_brief" / "fonts" / "MiSans-Medium.ttf"
BLACK, WHITE = 0, 255


# ---------------------------------------------------------------- 数据收集
def collect_brief(limit: int = 12) -> tuple[dict, dict, dict | None, dict | None]:
    brief = sources.collect_all(limit=limit)
    summary = sources.analyze_brief(brief)
    market = None
    review = None
    try:
        market, _freshness = sources.collect_market_for_push()
        review = sources.analyze_ashare(market)
    except Exception as exc:  # noqa: BLE001 - 行情失败不应阻断整张图
        print(f"行情获取失败：{exc}", file=sys.stderr)
    return brief, summary, market, review


def live_source_stats(brief: dict) -> tuple[int, int]:
    """返回（有非演示内容的源数，总源数）。演示标题与抓取结果逐条比对。"""
    live = 0
    for name, items in brief.items():
        demo = {t.strip().lower() for t in sources._DEMO.get(name, [])}
        titles = [str(i.get("title", "")).strip().lower() for i in items]
        if any(t and t not in demo for t in titles):
            live += 1
    return live, len(brief)


def pick_headlines(brief: dict, limit: int = 4) -> list[str]:
    """跨源去重挑头条：优先非演示标题，保持源顺序。"""
    out: list[str] = []
    seen: set[str] = set()
    for name, items in brief.items():
        demo = {t.strip().lower() for t in sources._DEMO.get(name, [])}
        real = [i for i in items if str(i.get("title", "")).strip().lower() not in demo]
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
    def __init__(self, font_path: Path):
        self.image = Image.new("L", (WIDTH, HEIGHT), WHITE)
        self.draw = ImageDraw.Draw(self.image)
        self.font_path = font_path
        self._cache: dict[tuple[int, int], ImageFont.FreeTypeFont] = {}

    def font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = (size, 1 if bold else 0)
        if key not in self._cache:
            self._cache[key] = ImageFont.truetype(str(self.font_path), size)
        return self._cache[key]

    def measure(self, text: str, size: int, bold: bool = False) -> int:
        return int(round(self.draw.textlength(text, font=self.font(size, bold))))

    def text(self, x: int, y: int, text: str, size: int = 11, bold: bool = False,
             fill: int = BLACK) -> int:
        self.draw.text((x, y), text, font=self.font(size, bold), fill=fill)
        return self.measure(text, size, bold)

    def wrap(self, text: str, size: int, max_width: int, max_lines: int = 2,
             bold: bool = False) -> list[str]:
        """按像素宽度折行；超出 max_lines 时末行加省略号。"""
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

    def rule(self, x1: int, y: int, x2: int, thickness: int = 1, fill: int = BLACK):
        self.draw.rectangle((x1, y, x2, y + thickness - 1), fill=fill)

    def box(self, x1: int, y: int, x2: int, y2: int, fill: int = BLACK):
        self.draw.rectangle((x1, y, x2, y2), fill=fill)

    def inverted(self, x: int, y: int, text: str, size: int = 11, bold: bool = True,
                 pad_x: int = 4, pad_y: int = 1) -> int:
        width = self.measure(text, size, bold)
        height = size + 3
        self.box(x, y, x + width + pad_x * 2 - 1, y + height + pad_y * 2 - 1)
        self.text(x + pad_x, y + pad_y - 1, text, size, bold, WHITE)
        return width + pad_x * 2

    def section(self, y: int, title: str, right: str = "") -> int:
        self.inverted(12, y, title, 11, True)
        if right:
            width = self.measure(right, 9)
            self.text(WIDTH - 12 - width, y + 2, right, 9, False)
        return y + 19

    def save(self, out_dir: Path, meta: dict) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        mono = self.image.point(lambda v: BLACK if v < 128 else WHITE).convert("1", dither=Image.NONE)
        mono.save(out_dir / "latest.png")
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
        header = MAGIC + struct.pack("<HH", WIDTH, HEIGHT)
        payload = header + bytes(buf)
        (out_dir / "latest.bin").write_bytes(payload)
        meta = dict(meta)
        meta.update({
            "width": WIDTH,
            "height": HEIGHT,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "crc32_bitmap": f"{zlib.crc32(bytes(buf)) & 0xffffffff:08x}",
        })
        (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


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


def short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


# ---------------------------------------------------------------- 页面
INDEX_SHORT_NAMES = {
    "上证指数": "上证",
    "深证成指": "深证",
    "创业板指": "创业板",
    "科创50": "科创50",
    "恒生指数": "恒生",
    "恒生科技指数": "恒生科技",
    "恒生国企指数": "国企",
    "道琼斯": "道指",
    "标普500": "标普",
    "纳斯达克": "纳指",
}

MARKET_SOURCE_LABELS = {
    "eastmoney": "东财",
    "tencent": "腾讯",
    "snapshot": "快照",
}

FOOTER_Y = 288


def draw_sheet(summary: dict, market: dict | None, review: dict | None,
               headlines: list[str], now: datetime, live: int, total: int,
               demo: bool) -> Sheet:
    sheet = Sheet(FONT_PATH)

    # ---------------------------------------------------------- 顶部黑条
    sheet.box(0, 0, WIDTH - 1, 30)
    sheet.text(12, 6, "章鱼 AI · 全景分析", 16, True, WHITE)
    date_text = now.strftime("%m月%d日 %H:%M")
    sheet.text(WIDTH - 14 - sheet.measure(date_text, 9), 4, date_text, 9, False, WHITE)
    sheet.text(WIDTH - 14 - sheet.measure("每日更新", 8), 17, "每日更新", 8, False, WHITE)

    bias = str(summary.get("bias") or "中性")
    bull = int(summary.get("bull") or 50)
    bear = 100 - bull
    signals = int(summary.get("signals") or 0)
    sectors_up = " / ".join(summary.get("sectors_up") or []) or "暂无"
    sectors_down = " / ".join(summary.get("sectors_down") or []) or "暂无"
    flow = str(summary.get("flow") or "").split("；")[0]
    sentiment = str(summary.get("headline") or "")

    # ---------------------------------------------------------- AI 每日总结
    y = sheet.section(33, "AI 每日总结", f"{bias}  {bull}% : {bear}%  ·  {signals} 信号")
    summary_rows = [
        ("市场情绪", sentiment),
        ("多空博弈", f"多方 {bull}% · 空方 {bear}% · 信号 {signals} 条"),
        ("板块方向", f"利好 {sectors_up}｜承压 {sectors_down}"),
        ("资金流向", flow),
    ]
    for label, body in summary_rows:
        sheet.text(12, y, label, 10, True)
        line = sheet.wrap(body, 10, 310, 1)
        if line:
            sheet.text(72, y, line[0], 10)
        y += 13
    assert y <= 106, "summary block overflow"

    # ---------------------------------------------------------- AI 看盘 · A股
    sheet.rule(12, 105, WIDTH - 13, 1)
    review_date = str((review or {}).get("date") or (market or {}).get("date") or "--")
    src = str((review or {}).get("source") or (market or {}).get("source") or "")
    tag = MARKET_SOURCE_LABELS.get(src, src or "快照")
    y = sheet.section(107, "AI 看盘 · A股", f"复盘日 {review_date} · {tag}")
    indices = (review or {}).get("indices") or []
    if indices:
        for i, item in enumerate(indices[:4]):
            x = 12 + (i % 2) * 190
            yy = y + (i // 2) * 15
            raw_name = str(item.get("name") or "")
            name = INDEX_SHORT_NAMES.get(raw_name, raw_name)
            close = item.get("close")
            pct = fmt_pct(item.get("pct"))
            close_text = f"{float(close):,.2f}" if isinstance(close, (int, float)) else "--"
            sheet.text(x, yy, name, 10, True)
            name_w = sheet.measure(name, 10, True)
            sheet.text(x + name_w + 4, yy, close_text, 9)
            sheet.text(x + 184 - sheet.measure(pct, 9, True), yy, pct, 9, True)
        y += 32
        turnover = (review or {}).get("turnover") or {}
        breadth = (review or {}).get("breadth") or {}
        parts = []
        if turnover.get("amount") is not None:
            parts.append(f"成交 {fmt_amount(turnover.get('amount'))}")
        if breadth.get("limit_up") is not None:
            parts.append(f"涨停 {breadth.get('limit_up')}")
        if breadth.get("limit_down") is not None:
            parts.append(f"跌停 {breadth.get('limit_down')}")
        if parts:
            sheet.text(12, y, " · ".join(parts), 9)
        leaders = [str(x.get("name") or "") for x in (review or {}).get("leaders") or []][:2]
        laggards = [str(x.get("name") or "") for x in (review or {}).get("laggards") or []][:1]
        sector_text = ""
        if leaders:
            sector_text = "领涨 " + "、".join(leaders)
        if laggards:
            sector_text += ("　领跌 " if sector_text else "领跌 ") + "、".join(laggards)
        if sector_text:
            line = sheet.wrap(sector_text, 9, 376, 1)
            if line:
                sheet.text(12, y + 11, line[0], 9)
        y += 23
    else:
        sheet.text(12, y, "今日未获取 A 股行情", 10)
        y += 16
    assert y <= 181, "market block overflow"

    # ---------------------------------------------------------- AI 板块机会
    sheet.rule(12, 182, WIDTH - 13, 1)
    y = sheet.section(184, "AI 板块机会")
    cards = []
    for item in (summary.get("opportunity_sectors") or [])[:2]:
        cards.append((str(item.get("tag") or ""), int(item.get("net") or 0),
                      int(item.get("sources") or 0), "机会"))
    for item in (summary.get("pressure_sectors") or [])[:1]:
        cards.append((str(item.get("tag") or ""), int(item.get("net") or 0),
                      int(item.get("sources") or 0), "承压"))
    if cards:
        card_width = 121
        for i, (tag_text, net, src_count, kind) in enumerate(cards[:3]):
            x = 12 + i * 125
            y2 = y + 24
            sheet.draw.rectangle((x, y, x + card_width, y2), outline=BLACK)
            sheet.text(x + 6, y + 2, tag_text, 10, True)
            net_text = f"净多 {net:+d}" if kind == "机会" else f"净空 {abs(net)}"
            sheet.text(x + 6, y + 13, f"{net_text} · {src_count} 源", 9)
        y += 27
    else:
        sheet.text(12, y, "暂无净多头信号占优的板块", 10)
        y += 16
    assert y <= 231, "sector block overflow"

    # ---------------------------------------------------------- 全网快讯
    sheet.rule(12, 232, WIDTH - 13, 1)
    y = sheet.section(234, "全网快讯")
    for title in headlines[:3]:
        sheet.draw.rectangle((12, y + 4, 14, y + 6), fill=BLACK)
        sheet.text(19, y, short(title, 38), 9)
        y += 11
    assert y <= FOOTER_Y - 2, "headline block overflow"

    # ---------------------------------------------------------- 页脚
    sheet.rule(12, FOOTER_Y, WIDTH - 13, 1)
    left = "离线演示数据" if demo else f"实时源 {live}/{total}"
    sheet.text(12, FOOTER_Y + 2, left, 7)
    footer = "仅供参考 · 不构成投资建议"
    sheet.text(WIDTH - 12 - sheet.measure(footer, 7), FOOTER_Y + 2, footer, 7)
    return sheet


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(ROOT / "device_brief" / "out"))
    parser.add_argument("--demo", action="store_true", help="仅用内置演示数据（本地预览）")
    parser.add_argument("--require-live", action="store_true", help="实时源不足时退出 3")
    parser.add_argument("--min-live-sources", type=int, default=20)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()

    started = time.time()
    brief, summary, market, review = collect_brief(limit=args.limit)
    live, total = live_source_stats(brief)
    demo = live == 0
    if args.demo:
        demo = True
    print(f"采集完成：{total} 源，实时 {live} 源，用时 {time.time() - started:.1f}s")
    if args.require_live and live < args.min_live_sources:
        print(f"实时源不足（{live} < {args.min_live_sources}），放弃更新设备简报", file=sys.stderr)
        return 3

    now = datetime.now()
    headlines = headline_candidates(brief, 6)
    sheet = draw_sheet(summary, market, review, headlines, now, live, total, demo)
    meta = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "bias": summary.get("bias"),
        "bull": summary.get("bull"),
        "bear": summary.get("bear"),
        "signals": summary.get("signals"),
        "live_sources": live,
        "total_sources": total,
        "demo": demo,
        "market_date": (review or {}).get("date"),
        "market_source": (review or {}).get("source"),
        "headlines": headlines,
    }
    sheet.save(Path(args.out_dir), meta)
    print(f"已生成 {args.out_dir}/latest.bin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
