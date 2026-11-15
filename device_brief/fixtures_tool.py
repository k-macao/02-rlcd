#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 / 校验 fixtures/ 里跨语言共用的期望值文件。

服务器渲染器（Python）与设备固件（C++）各自实现了一遍行情解析和数字格式；
两边的回归测试读同一批文件，保证面板在位图和真机自绘两种模式下显示逐字相同：

  expected_cells.txt  fixtures 里的实测响应解析、合并后，每行四个单元格的文字、
                      行情时间（UTC 纪元秒），以及页眉行情时间与页脚两市成交额
  format_cases.txt    点位 / 涨跌幅 / 成交量三种格式函数在边界值上的期望输出
  time_cases.txt      各数据源时间写法 → UTC 纪元秒（含美国夏令时切换日）
  panel_*.pbm         三张基准图（PBM P4，1 = 黑）：固件自绘结果必须与之逐像素相同

用法：
  python3 device_brief/fixtures_tool.py           # 校验文件与当前实现一致
  python3 device_brief/fixtures_tool.py --write   # 实现有意改动后重新生成（记得同步固件）
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import market_quotes as mq  # noqa: E402

EXPECTED_CELLS = mq.FIXTURE_DIR / "expected_cells.txt"
FORMAT_CASES = mq.FIXTURE_DIR / "format_cases.txt"
TIME_CASES = mq.FIXTURE_DIR / "time_cases.txt"

PRICE_VALUES = (
    0.0, -1.0, 0.004, 0.005, 1.0, 9.995, 99.999, 699.66, 999.995, 1000.0, 3634.353,
    7561.6699, 23637.6, 61142.13, 99999.995, 123456.789, 1234567.891, 9999999.99,
    999999999.99, 1e9, 1e30,
)
PCT_VALUES = (
    0.0, 0.004, -0.004, 0.005, -0.005, 0.01, -0.01, 0.1, -0.59, 0.53, 1.0, -2.05,
    9.995, -9.995, 10.0, -10.05, 49.99, -3.32, 0.999, 0.994, 999999.99, 1e6, -1e6,
)
CN_VALUES = (
    0.0, -5.0, 1.0, 999.4, 9999.0, 9999.4, 9999.5, 10000.0, 12345.0, 99949.0, 99950.0,
    999499.0, 999500.0, 9994999.0, 99994999.0, 99995000.0, 1e8, 6.45244111e8, 9.994e8,
    9.995e8, 15.17e8, 99.94e8, 99.95e8, 472.29e8, 999.4e8, 999.5e8, 9999.4e8, 9999.5e8,
    1e12, 1.655668e12, 9.994e12, 9.995e12, 16.718835e12, 99.94e12, 99.95e12, 999.5e12,
    1234.5e12, 123456e12, 47229365700.0, 770614170000.0, 885054040000.0,
    195857007140.0, 11525719783.0, 1785916000.0, 2741127000.0, 247829000.0,
    9.99e17, 1e18, 1e300,
)


def epoch_text(moment) -> str:
    return str(int(moment.timestamp())) if moment is not None else "0"


def expected_cells_text() -> str:
    """fixtures 实测响应 → 面板 12 行文字与行情时间 + 页眉行情时间 + 页脚两市成交。"""
    panel = mq.demo_panel()
    lines = []
    for row in panel.rows:
        quote = row.quote
        cells = (
            mq.fmt_price(quote.last), mq.fmt_pct(quote.pct),
            mq.fmt_cn_number(quote.volume), mq.fmt_cn_number(quote.turnover),
        ) if row.valid else ("--",) * 4
        lines.append("|".join(
            (row.spec.key, row.spec.market, row.spec.name) + cells
            + (epoch_text(quote.time if row.valid else None),)))
    lines.append("header|" + mq.fmt_as_of(panel.as_of))
    lines.append("footer|" + mq.two_market_text(panel.two_market_turnover()))
    return "\n".join(lines) + "\n"


def format_cases_text() -> str:
    """每行：函数|输入（repr，可被 strtod 精确读回）|期望输出。"""
    lines = []
    for value in PRICE_VALUES:
        lines.append(f"price|{value!r}|{mq.fmt_price(value)}")
    for value in PCT_VALUES:
        lines.append(f"pct|{value!r}|{mq.fmt_pct(value)}")
    for value in CN_VALUES:
        lines.append(f"cn|{value!r}|{mq.fmt_cn_number(value)}")
    return "\n".join(lines) + "\n"


# 每类写法对应固件里的一个解析入口（见 market_panel_contract_host_test.cpp）。
TIME_INPUTS = {
    # 腾讯沪深 [30]：紧凑写法，北京时间
    "compact_cn": (
        "20261113161417", "20260102093000", "20240229150003", "19700101080000",
        "20261113", "2026111316141", "20261313161417", "20260230101010",
        "20261113246060", "abcdefghijklmn", "",
    ),
    # 腾讯港股 [30]：斜杠写法，香港时间
    "slash_cn": (
        "2026/11/13 16:08:27", "2026/1/5 9:30:00", "2026/11/13  16:08:27",
        "2026-11-13 16:08:27", "2026/11/13", "2026/11/13 16:08", "2026/02/30 10:00:00",
        "2026/11/13 24:00:00", "",
    ),
    # 腾讯环球指数 [2]：横杠写法，北京时间
    "dash_cn": (
        "2026-11-13 14:30:01", "2026-03-08 02:30:00", "2026/11/13 14:30:01",
        "2026-11-13T14:30:01", "2026-11-13 14:30:61", "",
    ),
    # 腾讯美股 [30]：横杠写法，美国东部时间（3 月第二个周日、11 月第一个周日切换夏令时）
    "dash_us": (
        "2026-11-13 16:15:59", "2026-07-01 09:30:00", "2026-01-02 16:00:00",
        "2026-03-07 23:59:59", "2026-03-08 01:59:59", "2026-03-08 02:30:00",
        "2026-03-08 03:00:00", "2026-03-09 09:30:00",
        "2026-10-31 16:00:00", "2026-11-01 00:59:59", "2026-11-01 01:30:00",
        "2026-11-01 02:00:00", "2026-11-02 09:30:00",
        "2024-03-10 03:00:00", "2024-03-09 16:00:00", "2024-11-03 01:59:59",
        "2024-11-03 02:00:00", "2025-03-09 12:00:00", "2025-11-02 12:00:00",
        "2027-03-14 12:00:00", "2027-11-07 12:00:00", "2030-03-10 12:00:00",
        "2030-11-03 12:00:00", "2032-02-29 12:00:00", "2026-13-01 12:00:00", "",
    ),
    # Naver localTradedAt：ISO 8601 带时区
    "iso": (
        "2026-11-13T20:15:00+09:00", "2026-11-13T15:45:03+09:00",
        "2026-11-13T15:30:02.123456+09:00", "2026-11-13T06:30:00Z",
        "2026-11-13T01:30:00-05:00", "2026-11-13 20:15:00+09:00",
        "2026-11-13T20:15:00+0900", "2026-11-13T20:15:00+09",
        "2026-11-13T20:15:00", "2026-11-13", "2026-11-13T25:15:00+09:00",
        "2026-02-30T10:00:00+09:00", "not-a-time", "",
    ),
}


def time_case_epoch(kind: str, text: str) -> str:
    """用数据层自己的解析函数求期望值；解析不了的写 0（固件同样视为未知）。"""
    if kind == "compact_cn":
        moment = mq._parse_time(text, "%Y%m%d%H%M%S", mq.TZ_CN)
        # strptime 会靠回溯把少一位的串也拼出一个时间；行情接口不会给这种写法，
        # 固件按「必须恰好 14 位数字」处理，这里与之对齐。
        if moment is not None and not (len(text.strip()) == 14 and text.strip().isdigit()):
            moment = None
    elif kind == "slash_cn":
        moment = mq._parse_time(text, "%Y/%m/%d %H:%M:%S", mq.TZ_HK)
    elif kind == "dash_cn":
        moment = mq._parse_time(text, "%Y-%m-%d %H:%M:%S", mq.TZ_CN)
    elif kind == "dash_us":
        moment = mq._parse_time(text, "%Y-%m-%d %H:%M:%S", mq.TZ_US)
    else:
        moment = mq._parse_iso_time(text)
    return epoch_text(moment)


def time_cases_text() -> str:
    """每行：写法|原文|UTC 纪元秒（0 = 无法解析）。"""
    lines = []
    for kind, texts in TIME_INPUTS.items():
        for text in texts:
            lines.append(f"{kind}|{text}|{time_case_epoch(kind, text)}")
    return "\n".join(lines) + "\n"


# 固件自绘的回归基准图：(文件名, 参与的数据源, 页脚右侧状态文字)。
# 固件主机测试 market_panel_view_host_test.cpp 里有一张同样的表。
ALL_SOURCES = (mq.SOURCE_TENCENT, mq.SOURCE_SINA, mq.SOURCE_NAVER_KR, mq.SOURCE_NAVER_JP)
PANEL_IMAGES = (
    # 四个源齐全、页脚右侧留白：与设备负载（位图兜底）逐位相同
    ("panel_plain.pbm", ALL_SOURCES, ""),
    # 直连行情刷新及时
    ("panel_live.pbm", ALL_SOURCES, "每分钟更新"),
    # 只有腾讯一个源：日韩没有成交量、东证指数整行为空；状态里数字与汉字混排
    ("panel_partial.pbm", (mq.SOURCE_TENCENT,), "12 分钟未更新"),
)


def panel_image_bytes(sources, footer_right: str) -> bytes:
    import render_brief  # 延迟导入：只有生成 / 校验基准图才需要 Pillow 与 FreeType

    texts = {source: mq.fixture_text(source) for source in sources}
    sheet = render_brief.validate(
        render_brief.draw_panel(mq.build_panel(texts), footer_right))
    header = f"P4\n{render_brief.WIDTH} {render_brief.HEIGHT}\n".encode("ascii")
    return header + sheet.to_bitmap()


GENERATED = {
    EXPECTED_CELLS: expected_cells_text,
    FORMAT_CASES: format_cases_text,
    TIME_CASES: time_cases_text,
}
GENERATED_BINARY = {
    mq.FIXTURE_DIR / name: (lambda sources=sources, status=status:
                            panel_image_bytes(sources, status))
    for name, sources, status in PANEL_IMAGES
}


def stale_files() -> list[str]:
    stale = []
    for path, build in GENERATED.items():
        if not path.is_file() or path.read_text(encoding="utf-8") != build():
            stale.append(path.name)
    for path, build_bytes in GENERATED_BINARY.items():
        if not path.is_file() or path.read_bytes() != build_bytes():
            stale.append(path.name)
    return stale


def main(argv: list[str]) -> int:
    if "--write" in argv:
        for path, build in GENERATED.items():
            path.write_text(build(), encoding="utf-8")
            print(f"已写入 {path}")
        for path, build_bytes in GENERATED_BINARY.items():
            path.write_bytes(build_bytes())
            print(f"已写入 {path}")
        return 0
    stale = stale_files()
    if stale:
        print("fixtures 与当前实现不一致：" + "、".join(stale), file=sys.stderr)
        print("确认改动是有意的之后运行 fixtures_tool.py --write，并同步固件侧实现", file=sys.stderr)
        return 1
    print("fixtures 与当前实现一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
