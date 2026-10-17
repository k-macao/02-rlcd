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
# 网页与可用设备字体统一优先使用 Microsoft YaHei；设备渲染机若未安装，
# 回退到仓库内的 MiSans，以保证离线/CI 渲染始终可用。
FONT_FALLBACK_PATH = ROOT / "device_brief" / "fonts" / "MiSans-Medium.ttf"
_YAHEI_CANDIDATES = (
    Path(os.environ["MICROSOFT_YAHEI_FONT"]) if os.environ.get("MICROSOFT_YAHEI_FONT") else None,
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/msyh.ttf"),
    Path("/System/Library/Fonts/Microsoft/微软雅黑.ttf"),
    Path("/usr/share/fonts/truetype/microsoft/微软雅黑.ttf"),
    ROOT / "device_brief" / "fonts" / "MicrosoftYaHei.ttf",
)
FONT_PATH = next((path for path in _YAHEI_CANDIDATES if path and path.is_file()), FONT_FALLBACK_PATH)
# 兼容原有测试/调用方的常量名；实际渲染不再区分中文、数字或粗体字体。
FONT_CHINESE_PATH = FONT_PATH
FONT_DIGITAL_PATH = FONT_PATH
FONT_DIGITAL_BOLD_PATH = FONT_PATH
BLACK, WHITE = 0, 255

# 字号下限：面板为 4.2" 400×300（约 120dpi），1px ≈ 0.21mm。
# 旧版正文 9~10px、页脚 7px，汉字只有约 2mm 高，1bpp 阈值化后笔画粘连看不清。
# 新版正文 13px、次要信息 12px、页眉元信息最低 10px，不再使用更小的字号；
# 版面放不下时删内容（见 draw_sheet 的取舍），不许靠缩小字号硬塞。
# 全文采用常规字重，通过字号层级建立视觉结构（18→14→13→12→11→10）。
MARGIN = 10
CONTENT_W = WIDTH - MARGIN * 2
FONT_TITLE = 18
FONT_SECTION = 14
FONT_BODY = 13
FONT_SMALL = 12
FONT_META = 11
FONT_MIN = 10

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
    def __init__(self, font_path: Path | None = None):
        self.image = Image.new("L", (WIDTH, HEIGHT), WHITE)
        self.draw = ImageDraw.Draw(self.image)
        self.font_path = font_path or FONT_PATH
        # 所有字符使用同一个常规字重字体；保留旧字段仅兼容外部调用。
        self.font_chinese_path = self.font_path
        self.font_digital_path = self.font_path
        self.font_digital_bold_path = self.font_path
        # 缓存键：(字体路径, 字号)
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def _font(self, is_digital: bool, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        # bold/is_digital 参数保留兼容旧接口，但简报统一使用微软雅黑常规字重。
        key = (str(self.font_path), size)
        if key not in self._cache:
            self._cache[key] = ImageFont.truetype(str(self.font_path), size)
        return self._cache[key]

    def _is_digital_char(self, ch: str) -> bool:
        # 兼容旧接口；数值不再切换到另一套等宽字体。
        return False

    def _runs(self, text: str):
        if text:
            yield False, text

    def font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        return self._font(False, size)

    def measure(self, text: str, size: int, bold: bool = False) -> int:
        # 不论调用方传入何种字重标记，都按常规字重计算实际文本宽度。
        return int(round(self.draw.textlength(text, font=self._font(False, size))))

    def text(self, x: int, y: int, text: str, size: int = 11, bold: bool = False,
             fill: int = BLACK) -> int:
        # 统一字体、常规字重；不描边、不叠影，bold 参数仅为兼容保留。
        font = self._font(False, size)
        self.draw.text((x, y), text, font=font, fill=fill)
        return int(round(self.draw.textlength(text, font=font)))

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
        # 反白标题条高 = 字号 + 3 + 上下各 2px 内边距，再留 3px 与正文分隔。
        return y + FONT_SECTION + 10

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


def sector_line(sheet: Sheet, summary: dict, max_width: int) -> str:
    """板块方向一行：优先「名称±净信号·源数」，放不下逐级降为净信号、纯名称。

    旧版这里另有「AI 板块机会」三张卡片，与板块方向行内容重复；
    大字版把卡片的净信号/源数并进本行，删掉整个卡片区换字号。
    """
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

def draw_sheet(summary: dict, market: dict | None, review: dict | None,
               headlines: list[str], now: datetime, live: int, total: int,
               demo: bool) -> Sheet:
    """大字版排版：正文 13px 起步，放不下的内容按下面取舍删，而不是缩字号。

    与旧版（正文 9~10px）的取舍：
    - 「多空博弈」整行删除：与本区标题右侧的多空比例/信号数完全重复；
    - 「资金流向」整行删除：源头 _compose_flow 由利好/承压板块拼成，
      与「板块方向」行重复（网页版与推送仍保留四行全文）；
    - 「AI 板块机会」三张卡片区删除：净信号/源数并进「板块方向」行；
    - 页脚 7px 小字删除：实时源数移入页眉黑条，免责声明移到
      「全网快讯」标题右侧（11px），位置变了但全页可见。
    快讯改按像素宽折行截断：英文标题实际能显示的字数反而多于旧版按字数截 38。
    """
    sheet = Sheet(FONT_PATH)
    right_edge = WIDTH - MARGIN

    # ---------------------------------------------------------- 顶部黑条
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

    # ---------------------------------------------------------- AI 每日总结
    y = sheet.section(37, "AI 每日总结",
                      f"{bias} 多{bull}% 空{bear}% · {signals}信号")
    # 总结句给两行整宽：旧版单行必然截断到「…」，结论（后半句）从来显示不全。
    for line in sheet.wrap(sentiment or "暂无市场总结", FONT_BODY, CONTENT_W, 2):
        sheet.text(MARGIN, y, line, FONT_BODY)
        y += 18
    sheet.text(MARGIN, y, "板块", FONT_BODY, True)
    body_x = MARGIN + sheet.measure("板块", FONT_BODY, True) + 6
    sheet.text(body_x, y, sector_line(sheet, summary, right_edge - body_x),
               FONT_BODY)
    y += 18
    assert y <= 124, "summary block overflow"

    # ---------------------------------------------------------- AI 看盘 · A股
    sheet.rule(MARGIN, y + 1, right_edge - 1, 1)
    review_date = str((review or {}).get("date") or (market or {}).get("date") or "--")
    src = str((review or {}).get("source") or (market or {}).get("source") or "")
    tag = MARKET_SOURCE_LABELS.get(src, src or "快照")
    y = sheet.section(y + 4, "AI 看盘 · A股", f"{review_date} · {tag}")
    indices = (review or {}).get("indices") or []
    if indices:
        shown = indices[:4]
        for i, item in enumerate(shown):
            # 两栏各宽 184、栏间留 12px，否则左栏涨跌幅与右栏名称会连成一片。
            x = MARGIN + (i % 2) * 196
            yy = y + (i // 2) * 19
            raw_name = str(item.get("name") or "")
            name = INDEX_SHORT_NAMES.get(raw_name, raw_name)
            close = item.get("close")
            pct = fmt_pct(item.get("pct"))
            close_text = f"{float(close):,.2f}" if isinstance(close, (int, float)) else "--"
            sheet.text(x, yy, name, FONT_BODY, True)
            name_w = sheet.measure(name, FONT_BODY, True)
            sheet.text(x + name_w + 5, yy + 1, close_text, FONT_SMALL)
            sheet.text(x + 184 - sheet.measure(pct, FONT_SMALL, True), yy + 1,
                       pct, FONT_SMALL, True)
        y += ((len(shown) + 1) // 2) * 19 + 2
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
            sheet.text(MARGIN, y, " · ".join(parts), FONT_SMALL)
            y += 16
        leaders = [str(x.get("name") or "") for x in (review or {}).get("leaders") or []][:2]
        laggards = [str(x.get("name") or "") for x in (review or {}).get("laggards") or []][:1]
        sector_text = ""
        if leaders:
            sector_text = "领涨 " + "、".join(leaders)
        if laggards:
            sector_text += ("　领跌 " if sector_text else "领跌 ") + "、".join(laggards)
        if sector_text:
            line = sheet.wrap(sector_text, FONT_SMALL, CONTENT_W, 1)
            if line:
                sheet.text(MARGIN, y, line[0], FONT_SMALL)
            y += 16
    else:
        sheet.text(MARGIN, y, "今日未获取 A 股行情", FONT_BODY)
        y += 18
    assert y <= 228, "market block overflow"

    # ---------------------------------------------------------- 全网快讯
    sheet.rule(MARGIN, y + 1, right_edge - 1, 1)
    y = sheet.section(y + 4, "全网快讯", "仅供参考 · 不构成投资建议",
                      right_size=FONT_META)
    if headlines:
        for title in headlines[:3]:
            sheet.draw.rectangle((MARGIN, y + 5, MARGIN + 3, y + 8), fill=BLACK)
            line = sheet.wrap(title, FONT_SMALL, CONTENT_W - 10, 1)
            if line:
                sheet.text(MARGIN + 10, y, line[0], FONT_SMALL)
            y += 16
    else:
        sheet.text(MARGIN, y, "暂无快讯", FONT_SMALL)
        y += 16
    assert y <= HEIGHT - 4, "headline block overflow"
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
