#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成行情数据面板在设备上自绘用的两套 16px 1bpp LVGL 字库。

设备自绘面板必须和服务器位图（device_brief/render_brief.py）用同一套字形：
阿里巴巴普惠体 3.0，文字 Regular、数值串与标题 Bold，FreeType MONO + 自动 hinting
（lv_font_conv 在 bpp=1 时的默认行为）。字库只收面板实际用到的字，
清单在下面两个常量里；界面新增文字后把字补进来再重新生成。

用法（在仓库根目录运行，需要 Node.js）：
  python3 RLCD_CLOCK/tools/gen_market_panel_fonts.py
  LV_FONT_CONV="node /path/to/lv_font_conv" python3 RLCD_CLOCK/tools/gen_market_panel_fonts.py

生成后运行主机测试 market_panel_view_test：它会逐条检查界面文字的字形覆盖，
并把设备自绘结果与服务器渲染器的位图逐像素比对。
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FONT_DIR = Path("device_brief/fonts")
ASSET_DIR = Path("RLCD_CLOCK/main/assets")
LV_FONT_CONV_VERSION = "1.5.3"

# Regular：全部可打印 ASCII，加上面板、页脚状态和占位提示里的汉字与间隔号。
REGULAR_ASCII_RANGE = "0x20-0x7E"
REGULAR_TEXT = (
    "指数最新涨跌幅成交量成交额"          # 列标题
    "上证指数深证成指创业板指"            # A
    "恒生指数恒生科技"                    # 港
    "道琼斯纳斯达克标普"                  # 美
    "日经东证指数"                        # 日
    "万亿"                                # 数量单位
    "两市成交"                            # 页脚左侧
    "每分钟更新分钟未更新"                # 页脚右侧：直连状态
    "低电量暂停更新离线模式"              # 页脚右侧：暂停原因
    "读取"                                # 页脚右侧：位图兜底
    "行情数据尚未获取"                    # 占位提示
    "立即刷新长按进入设置"                # 按键提示
    "·"
)
# Bold：数值串（数字与正负号、小数点、千分位、百分号、冒号）、顶栏标题、市场色块。
BOLD_TEXT = (
    " 0123456789+-.,%:/"
    "全球行情"
    "A港美韩日"
)

# (字库名, 字体文件, ASCII 区间, 额外字符, 生成文件首行的用途说明)
FONTS = (
    ("market_panel_text_16", "AlibabaPuHuiTi-3-55-Regular.ttf", REGULAR_ASCII_RANGE, REGULAR_TEXT,
     "行情数据面板的文字字库：阿里巴巴普惠体 3.0 Regular 16px 单色子集。"),
    ("market_panel_bold_16", "AlibabaPuHuiTi-3-85-Bold.ttf", None, BOLD_TEXT,
     "行情数据面板的数值与标题字库：阿里巴巴普惠体 3.0 Bold 16px 单色子集。"),
)
GENERATED_NOTE = "由 RLCD_CLOCK/tools/gen_market_panel_fonts.py 生成，请勿手改。"


def unique_sorted(text: str) -> str:
    return "".join(sorted(set(text)))


def converter_command() -> list[str]:
    override = os.environ.get("LV_FONT_CONV", "").strip()
    if override:
        return shlex.split(override)
    return ["npx", "--yes", f"lv_font_conv@{LV_FONT_CONV_VERSION}"]


def codepoint_ranges(text: str) -> str:
    return ",".join(f"0x{ord(char):X}" for char in unique_sorted(text))


def generate(name: str, font_file: str, ascii_range: str | None, text: str,
             purpose: str) -> None:
    ranges = codepoint_ranges(text)
    if ascii_range:
        ranges = f"{ascii_range},{ranges}"
    command = converter_command() + [
        "--font", str(FONT_DIR / font_file),
        "-r", ranges,
        "--size", "16", "--bpp", "1", "--format", "lvgl", "--no-kerning",
        "--no-compress",
        "--lv-include", "lvgl.h",
        "--lv-font-name", name,
        "--output", str(ASSET_DIR / f"{name}.c"),
    ]
    # 用相对路径调用：生成文件头部的 Opts 注释里不会带上本机绝对路径。
    subprocess.run(command, cwd=ROOT, check=True)
    # 仓库约定每个源文件首行说明用途；转换器不会写，这里补上。
    output = ROOT / ASSET_DIR / f"{name}.c"
    body = output.read_text(encoding="utf-8")
    output.write_text(f"// {purpose}{GENERATED_NOTE}\n{body}", encoding="utf-8")
    print(f"已生成 {ASSET_DIR / (name + '.c')}")


def main() -> int:
    for name, font_file, ascii_range, text, purpose in FONTS:
        generate(name, font_file, ascii_range, text, purpose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
