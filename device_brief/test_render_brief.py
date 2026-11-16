#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全球行情数据面板的回归测试（不联网，全部用 fixtures/ 里留档的实测响应）。

    python3 -m pip install pillow freetype-py
    python3 device_brief/test_render_brief.py

回归重点：
- 四个行情接口的解析、单位换算与多源合并规则（改接口字段必须先改这里）；
- 点位 / 涨跌幅 / 成交量三种数字格式的边界值；
- 面板排版：400×300、全页 16px、只有纯黑 / 纯白、不叠字、不越界、
  页脚右侧给设备留白；
- 设备负载仍是固件约定的 AIB1 单页 15008 字节，行情不变就不重写文件；
- 行情不足时不覆盖上一版；
- 服务器渲染器与固件各有一份的常量（版面坐标、固定文字、接口地址、行顺序、
  阈值）必须逐项相同，fixtures/ 里的跨语言期望值与基准图必须是最新的。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures_tool  # noqa: E402
import market_quotes as mq  # noqa: E402
import render_brief  # noqa: E402

ROOT = render_brief.ROOT
FIRMWARE = ROOT / "RLCD_CLOCK" / "main"
ALL_SOURCES = (mq.SOURCE_TENCENT, mq.SOURCE_SINA, mq.SOURCE_NAVER_KR, mq.SOURCE_NAVER_JP)


def panel_from(*sources: str) -> mq.PanelData:
    """只用指定数据源的 fixtures 组装面板，模拟其余数据源失败。"""
    return mq.build_panel({source: mq.fixture_text(source) for source in sources})


def cells_by_key(panel: mq.PanelData) -> dict:
    return {row.spec.key: render_brief.row_cells(row) for row in panel.rows}


def run_main(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = render_brief.main(argv)
        except SystemExit as exc:  # argparse 用 SystemExit 报参数错误
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


class QuoteParsingTest(unittest.TestCase):
    """实测响应（2026-11-13 收盘）→ 面板 12 行。期望值是人工核对过的字面量。"""

    def test_fixture_panel_matches_reviewed_values(self):
        panel = mq.demo_panel()
        self.assertEqual(panel.live_rows, 12)
        self.assertEqual(panel.sources, {source: True for source in ALL_SOURCES})
        self.assertEqual(cells_by_key(panel), {
            "sh000001": ("3,634.35", "-0.59%", "472亿", "7,706亿"),
            "sz399001": ("10,968.79", "-2.05%", "559亿", "8,851亿"),
            "sz399006": ("2,426.08", "-3.32%", "170亿", "4,353亿"),
            "hkHSI": ("23,637.60", "-1.55%", "115亿", "1,959亿"),
            "hkHSTECH": ("3,813.19", "-3.02%", "15.2亿", "663亿"),
            "usDJI": ("52,347.55", "+0.10%", "6.45亿", "--"),
            "usIXIC": ("25,136.27", "-0.91%", "77.4亿", "--"),
            "usINX": ("7,561.67", "-0.40%", "27.3亿", "--"),
            "KOSPI": ("5,290.53", "-0.98%", "2.48亿", "16.7万亿"),
            "KOSDAQ": ("699.66", "+0.45%", "4.55亿", "5.79万亿"),
            "N225": ("61,142.35", "+0.53%", "17.9亿", "--"),
            "TOPX": ("3,941.57", "+0.11%", "27.4亿", "--"),
        })
        self.assertEqual(render_brief.header_time_text(panel), "11-14 06:15")
        self.assertEqual(render_brief.footer_left_text(panel), "两市成交 1.66万亿")

    def test_rows_follow_the_requested_market_order(self):
        self.assertEqual(
            [(spec.market, spec.name) for spec in mq.PANEL_ROWS],
            [("A", "上证指数"), ("A", "深证成指"), ("A", "创业板指"),
             ("港", "恒生指数"), ("港", "恒生科技"),
             ("美", "道琼斯"), ("美", "纳斯达克"), ("美", "标普500"),
             ("韩", "KOSPI"), ("韩", "KOSDAQ"),
             ("日", "日经225"), ("日", "东证指数")])
        self.assertEqual(mq.MARKET_ORDER, ("A", "港", "美", "韩", "日"))
        self.assertEqual(
            [group[0] for group in render_brief.market_groups(mq.demo_panel())],
            list(mq.MARKET_ORDER))

    def test_tencent_units(self):
        quotes = mq.parse_tencent(mq.fixture_text(mq.SOURCE_TENCENT))
        sh = quotes["sh000001"]
        # 沪深：成交量 手 → 股，成交额 万元 → 元。
        self.assertAlmostEqual(sh.volume, 472_293_657 * 100)
        self.assertAlmostEqual(sh.turnover, 77_061_417 * 1e4)
        self.assertEqual(sh.time, datetime(2026, 11, 13, 16, 14, 27, tzinfo=mq.TZ_CN))
        # 港股指数：腾讯只有成交额（万港元），没有成交股数。
        hsi = quotes["hkHSI"]
        self.assertIsNone(hsi.volume)
        self.assertAlmostEqual(hsi.turnover, 195_857_007_140.0, delta=1e4)
        # 美股指数：只有成交股数；成交额字段是无意义值，必须丢弃。
        dji = quotes["usDJI"]
        self.assertAlmostEqual(dji.volume, 645_244_111.0)
        self.assertIsNone(dji.turnover)
        # 美股时间是美国东部时间（11 月为冬令时 UTC-5）。
        self.assertEqual(dji.time.astimezone(timezone.utc),
                         datetime(2026, 11, 13, 21, 43, 26, tzinfo=timezone.utc))
        # 环球指数：只有点位与涨跌幅。
        for key in ("gzN225", "gzKS11", "gzKOSDAQ"):
            self.assertGreater(quotes[key].last, 0)
            self.assertIsNone(quotes[key].volume)
            self.assertIsNone(quotes[key].turnover)

    def test_sina_and_naver_units(self):
        sina = mq.parse_sina_hk(mq.fixture_text(mq.SOURCE_SINA))
        self.assertEqual(set(sina), {"hkHSI", "hkHSTECH"})
        self.assertAlmostEqual(sina["hkHSI"].volume, 11_525_719_783.0)
        # 新浪成交额单位是千港元，换算后应与腾讯同一量级。
        self.assertAlmostEqual(sina["hkHSI"].turnover, 195_857_007_140.0, delta=1e9)

        kr = mq.parse_naver(mq.fixture_text(mq.SOURCE_NAVER_KR))
        self.assertEqual(set(kr), {"KOSPI", "KOSDAQ"})
        self.assertAlmostEqual(kr["KOSPI"].volume, 247_829_000.0)
        self.assertAlmostEqual(kr["KOSPI"].turnover, 16_718_835e6)
        self.assertEqual(kr["KOSPI"].source, mq.SOURCE_NAVER_KR)

        jp = mq.parse_naver(mq.fixture_text(mq.SOURCE_NAVER_JP))
        self.assertEqual(set(jp), {"N225", "TOPX"})
        self.assertAlmostEqual(jp["N225"].volume, 1_785_916_000.0)
        # 日本指数的成交额字段不可信，一律不用。
        self.assertIsNone(jp["N225"].turnover)
        self.assertIsNone(jp["TOPX"].turnover)
        self.assertEqual(jp["N225"].source, mq.SOURCE_NAVER_JP)

    def test_garbage_input_yields_no_rows(self):
        for parser in (mq.parse_tencent, mq.parse_sina_hk, mq.parse_naver):
            for text in ("", "<html>502 Bad Gateway</html>", "{}", "[]", "null",
                         'v_sh000001="1~2~3";', 'var hq_str_rt_hkHSI="";',
                         '{"datas": "oops"}', '{"datas": [1, null, {}]}'):
                self.assertEqual(parser(text), {}, (parser.__name__, text))
        self.assertEqual(mq.parse_naver(None), {})

    def test_dirty_values_drop_the_row(self):
        text = mq.fixture_text(mq.SOURCE_TENCENT)
        line = next(item for item in text.split(";") if "v_sh000001=" in item)
        fields = line.split("~")

        def with_field(index: int, value: str) -> dict:
            changed = list(fields)
            changed[index] = value
            return mq.parse_tencent("~".join(changed) + ";")

        self.assertIn("sh000001", with_field(3, fields[3]))
        self.assertNotIn("sh000001", with_field(3, "0.00"))      # 点位为 0
        self.assertNotIn("sh000001", with_field(3, "-1"))        # 负点位
        self.assertNotIn("sh000001", with_field(3, "N/A"))
        self.assertNotIn("sh000001", with_field(3, "1e5"))       # 只接受纯十进制
        self.assertNotIn("sh000001", with_field(3, "nan"))
        self.assertNotIn("sh000001", with_field(32, "50.01"))    # 涨跌幅离谱
        self.assertNotIn("sh000001", with_field(32, ""))
        self.assertIn("sh000001", with_field(32, "-50"))
        # 成交量缺失只影响那一格，行情行仍然有效。
        quote = with_field(36, "")["sh000001"]
        self.assertIsNone(quote.volume)
        self.assertIsNotNone(quote.turnover)
        # 时间写法不对：行有效，时间未知。
        self.assertIsNone(with_field(30, "2026-11-13")["sh000001"].time)


class MergeRulesTest(unittest.TestCase):
    def test_tencent_only_keeps_prices_but_not_volumes(self):
        panel = panel_from(mq.SOURCE_TENCENT)
        cells = cells_by_key(panel)
        self.assertEqual(panel.live_rows, 11)
        self.assertEqual(panel.sources[mq.SOURCE_TENCENT], True)
        self.assertEqual(panel.sources[mq.SOURCE_SINA], False)
        # A 股、美股完全不受影响。
        self.assertEqual(cells["sh000001"], ("3,634.35", "-0.59%", "472亿", "7,706亿"))
        self.assertEqual(cells["usINX"], ("7,561.67", "-0.40%", "27.3亿", "--"))
        # 港股少了新浪的成交股数。
        self.assertEqual(cells["hkHSI"], ("23,637.60", "-1.55%", "--", "1,959亿"))
        # 日韩只剩腾讯环球指数的点位。
        self.assertEqual(cells["KOSPI"][2:], ("--", "--"))
        self.assertNotEqual(cells["KOSPI"][0], "--")
        self.assertEqual(cells["N225"][2:], ("--", "--"))
        self.assertNotEqual(cells["N225"][0], "--")
        # 东证指数只信 Naver。
        self.assertEqual(cells["TOPX"], ("--", "--", "--", "--"))

    def test_without_tencent_the_panel_is_not_live(self):
        panel = panel_from(mq.SOURCE_SINA, mq.SOURCE_NAVER_KR, mq.SOURCE_NAVER_JP)
        self.assertEqual(panel.live_rows, 6)
        self.assertLess(panel.live_rows, render_brief.DEFAULT_MIN_ROWS)
        cells = cells_by_key(panel)
        # 腾讯缺行时港股整行用新浪（点位、涨跌幅、成交量、成交额都有）。
        self.assertEqual(cells["hkHSI"], ("23,637.60", "-1.55%", "115亿", "1,959亿"))
        self.assertEqual(cells["sh000001"], ("--", "--", "--", "--"))
        self.assertEqual(render_brief.footer_left_text(panel), "两市成交 --")

    def test_hk_volume_comes_from_sina(self):
        tencent = mq.Quote(last=100.0, pct=1.0, turnover=5e9, source=mq.SOURCE_TENCENT)
        sina = mq.Quote(last=101.0, pct=2.0, volume=3e8, turnover=6e9, source=mq.SOURCE_SINA)
        merged = mq._merge_hk(tencent, sina)
        self.assertEqual((merged.last, merged.pct, merged.volume, merged.turnover),
                         (100.0, 1.0, 3e8, 5e9))
        self.assertEqual(mq._merge_hk(None, sina), sina)
        self.assertFalse(mq._merge_hk(None, None).valid)
        # 腾讯没给成交额时用新浪的。
        tencent.turnover = None
        self.assertEqual(mq._merge_hk(tencent, sina).turnover, 6e9)

    def test_n225_source_selection(self):
        moment = datetime(2026, 11, 13, 14, 0, tzinfo=mq.TZ_CN)
        naver = mq.Quote(last=61000.0, pct=0.5, volume=1.7e9, status="OPEN",
                         time=moment, source=mq.SOURCE_NAVER_JP)
        tencent = mq.Quote(last=61100.0, pct=0.66, source=mq.SOURCE_TENCENT)
        # 盘中：腾讯实时点位 + Naver 成交量。
        merged = mq._merge_n225(naver, tencent)
        self.assertEqual((merged.last, merged.pct, merged.volume), (61100.0, 0.66, 1.7e9))
        # 收盘后：Naver 的官方收盘价。
        naver.status = "CLOSE"
        self.assertIs(mq._merge_n225(naver, tencent), naver)
        # 两源相差超过 3%：腾讯的环球指数源异常，改信 Naver。
        naver.status = "OPEN"
        tencent.last = 61000.0 * 1.031
        self.assertIs(mq._merge_n225(naver, tencent), naver)
        tencent.last = 61000.0 * 1.029
        self.assertEqual(mq._merge_n225(naver, tencent).last, tencent.last)
        # 只有一个源。
        self.assertIs(mq._merge_n225(None, tencent), tencent)
        self.assertIs(mq._merge_n225(naver, None), naver)
        self.assertFalse(mq._merge_n225(None, None).valid)

    def test_kr_prefers_naver(self):
        naver = mq.Quote(last=5290.0, pct=-1.0, volume=2e8, turnover=1e13)
        tencent = mq.Quote(last=5291.0, pct=-0.9)
        self.assertIs(mq._merge_kr(naver, tencent), naver)
        self.assertIs(mq._merge_kr(None, tencent), tencent)
        self.assertFalse(mq._merge_kr(None, None).valid)

    def test_as_of_is_the_newest_quote_time(self):
        panel = mq.demo_panel()
        newest = max(row.quote.time for row in panel.rows if row.quote.time)
        self.assertEqual(panel.as_of, newest)
        self.assertEqual(mq.fmt_as_of(None), "--")
        empty = mq.build_panel({})
        self.assertIsNone(empty.as_of)
        self.assertEqual(empty.live_rows, 0)

    def test_collect_panel_isolates_source_failures(self):
        def fetch(url, encoding="utf-8", headers=None):
            if url == mq.NAVER_KR_URL:
                raise OSError("simulated timeout")
            if url == mq.SINA_HK_URL:
                # 新浪必须带 Referer，否则接口返回 403。
                self.assertEqual(headers, {"Referer": mq.SINA_REFERER})
            for source, source_url, *_ in mq.SOURCES:
                if source_url == url:
                    return mq.fixture_text(source)
            raise AssertionError(url)

        panel = mq.collect_panel(fetch)
        self.assertFalse(panel.demo)
        self.assertEqual(panel.sources[mq.SOURCE_NAVER_KR], False)
        self.assertEqual(panel.sources[mq.SOURCE_TENCENT], True)
        self.assertEqual(panel.live_rows, 12)   # 韩国两行退回腾讯的点位
        self.assertEqual(cells_by_key(panel)["KOSPI"][2:], ("--", "--"))


class NumberFormatTest(unittest.TestCase):
    def test_price(self):
        self.assertEqual(mq.fmt_price(61142.345), "61,142.35")
        self.assertEqual(mq.fmt_price(699.66), "699.66")
        self.assertEqual(mq.fmt_price(1234567.891), "1,234,567.89")
        for bad in (None, 0.0, -1.0, float("nan"), float("inf"), 1e9):
            self.assertEqual(mq.fmt_price(bad), "--")

    def test_pct_always_signed(self):
        self.assertEqual(mq.fmt_pct(0.53), "+0.53%")
        self.assertEqual(mq.fmt_pct(-2.05), "-2.05%")
        self.assertEqual(mq.fmt_pct(0.0), "0.00%")
        self.assertEqual(mq.fmt_pct(-0.004), "0.00%")   # 四舍五入后为零不带负号
        self.assertEqual(mq.fmt_pct(0.005), "+0.01%")
        for bad in (None, float("nan"), float("-inf"), 1e6):
            self.assertEqual(mq.fmt_pct(bad), "--")

    def test_cn_number_units(self):
        self.assertEqual(mq.fmt_cn_number(9999.0), "9,999")
        self.assertEqual(mq.fmt_cn_number(9999.5), "1.00万")
        self.assertEqual(mq.fmt_cn_number(12345.0), "1.23万")
        self.assertEqual(mq.fmt_cn_number(999500.0), "100万")
        self.assertEqual(mq.fmt_cn_number(99995000.0), "1.00亿")
        self.assertEqual(mq.fmt_cn_number(6.45244111e8), "6.45亿")
        self.assertEqual(mq.fmt_cn_number(15.17e8), "15.2亿")
        self.assertEqual(mq.fmt_cn_number(472.29e8), "472亿")
        self.assertEqual(mq.fmt_cn_number(9999.4e8), "9,999亿")
        self.assertEqual(mq.fmt_cn_number(9999.5e8), "1.00万亿")
        self.assertEqual(mq.fmt_cn_number(1.655668e12), "1.66万亿")
        self.assertEqual(mq.fmt_cn_number(16.718835e12), "16.7万亿")
        for bad in (None, 0.0, -5.0, float("nan"), float("inf"), 1e18):
            self.assertEqual(mq.fmt_cn_number(bad), "--")

    def test_number_parser_is_strict(self):
        self.assertEqual(mq._to_float("3,634.35"), 3634.35)
        self.assertEqual(mq._to_float(" -0.59 "), -0.59)
        self.assertEqual(mq._to_float("+.5"), 0.5)
        self.assertEqual(mq._to_float(12), 12.0)
        for bad in (None, "", "-", "1e5", "inf", "nan", "0x10", "12 34", "1.2.3", "--"):
            self.assertIsNone(mq._to_float(bad), bad)


class PanelRenderTest(unittest.TestCase):
    def setUp(self):
        self.panel = mq.build_panel({source: mq.fixture_text(source) for source in ALL_SOURCES})
        self.sheet = render_brief.draw_panel(self.panel)

    def test_full_panel_passes_layout_validation(self):
        render_brief.validate(self.sheet)
        self.assertEqual(self.sheet.image.size, (400, 300))
        # 只有纯黑 / 纯白。
        histogram = self.sheet.image.histogram()
        self.assertEqual(sum(histogram[1:255]), 0)
        self.assertGreater(histogram[0], 5000)
        # 全页 16px。
        self.assertEqual({run["size"] for run in self.sheet.text_runs}, {16})
        # 标题 + 行情时间 + 5 个列标题 + 5 个市场标签 + 12 行 × 5 格 + 页脚 = 73 段文字。
        self.assertEqual(len(self.sheet.text_runs), 2 + 5 + 5 + 12 * 5 + 1)

    def test_every_degraded_panel_still_validates(self):
        combos = [
            (),
            (mq.SOURCE_TENCENT,),
            (mq.SOURCE_SINA,),
            (mq.SOURCE_NAVER_KR, mq.SOURCE_NAVER_JP),
            (mq.SOURCE_TENCENT, mq.SOURCE_SINA),
            (mq.SOURCE_TENCENT, mq.SOURCE_NAVER_JP),
        ]
        for sources in combos:
            sheet = render_brief.draw_panel(panel_from(*sources))
            render_brief.validate(sheet)
            self.assertEqual(len(sheet.to_bitmap()), 15000, sources)

    def test_widest_cells_do_not_collide(self):
        panel = self.panel
        for row in panel.rows:
            row.quote = mq.Quote(last=888888.88, pct=-48.88, volume=9.999e11,
                                 turnover=9.999e15,
                                 time=datetime(2026, 12, 28, 23, 58, tzinfo=mq.TZ_CN))
        sheet = render_brief.draw_panel(panel, "离线模式 暂停更新")
        render_brief.validate(sheet)
        self.assertEqual(render_brief.row_cells(panel.rows[0]),
                         ("888,888.88", "-48.88%", "9,999亿", "9,999万亿"))

    def test_overlapping_text_is_rejected(self):
        self.sheet.text(render_brief.NAME_X, render_brief.row_y(0), "叠字")
        with self.assertRaises(render_brief.LayoutError):
            render_brief.validate(self.sheet)

    def test_text_outside_the_canvas_is_rejected(self):
        self.sheet.text(390, 150, "越界文字")
        with self.assertRaises(render_brief.LayoutError):
            render_brief.validate(self.sheet)

    def test_footer_right_is_left_blank_for_the_device(self):
        x0, y0, x1, y1 = render_brief.DEVICE_OVERLAY_BOX
        region = self.sheet.image.crop((x0, y0, x1, y1))
        self.assertEqual(region.getextrema(), (255, 255))
        # 文档预览 / 固件基准图可以把状态文字画进去，位置在这块区域内。
        with_status = render_brief.draw_panel(self.panel, "每分钟更新")
        render_brief.validate(with_status)
        self.assertEqual(with_status.image.crop((x0, y0, x1, y1)).getextrema(), (0, 255))
        status_run = with_status.text_runs[-1]
        self.assertEqual(status_run["text"], "每分钟更新")
        self.assertGreaterEqual(status_run["bbox"][0], x0)
        self.assertLessEqual(status_run["bbox"][2], x1)

    def test_header_is_white_on_black(self):
        bitmap = self.sheet.to_bitmap()
        self.assertEqual(bitmap[0], 0xFF)                       # 左上角是黑条
        self.assertEqual(bitmap[50 * render_brief.HEADER_BAR_BOTTOM], 0xFF)
        self.assertEqual(bitmap[50 * (render_brief.HEADER_BAR_BOTTOM + 1)], 0x00)
        self.assertEqual(bitmap[-1], 0x00)                      # 右下角留白

    def test_demo_panel_is_labelled(self):
        demo = render_brief.draw_panel(mq.demo_panel())
        render_brief.validate(demo)
        self.assertEqual(demo.text_runs[0]["text"], "全球行情 · 离线演示")
        self.assertEqual(self.sheet.text_runs[0]["text"], "全球行情")

    def test_numeric_runs_use_bold(self):
        runs = lambda text: list(self.sheet._runs(text))  # noqa: E731
        self.assertEqual(runs("472亿"), [(True, "472"), (False, "亿")])
        self.assertEqual(runs("标普500"), [(False, "标普"), (True, "500")])
        self.assertEqual(runs("11-14 06:15"), [(True, "11-14"), (False, " "), (True, "06:15")])
        self.assertEqual(runs("+0.53%"), [(True, "+0.53%")])
        self.assertEqual(runs("--"), [(False, "--")])
        self.assertEqual(runs("两市成交 1.66万亿"),
                         [(False, "两市成交 "), (True, "1.66"), (False, "万亿")])


class PayloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "aibrief"
        self.panel = panel_from(*ALL_SOURCES)
        self.now = datetime(2026, 11, 14, 6, 20, tzinfo=mq.TZ_CN)

    def save(self, panel=None, now=None, preview=True):
        panel = panel or self.panel
        with redirect_stdout(io.StringIO()):
            return render_brief.save(render_brief.draw_panel(panel), self.out,
                                     render_brief.panel_meta(panel, now or self.now),
                                     preview=preview)

    def names(self) -> list[str]:
        return sorted(path.name for path in self.out.iterdir())

    def test_payload_is_a_single_aib1_page(self):
        meta = self.save()
        payload = (self.out / "latest.bin").read_bytes()
        self.assertEqual(len(payload), 15008)
        self.assertEqual(payload[:4], b"AIB1")
        self.assertEqual(struct.unpack("<HH", payload[4:8]), (400, 300))
        self.assertEqual(meta["pages"], 1)
        self.assertEqual(meta["bytes"], 15008)
        self.assertEqual(meta["sha256"], hashlib.sha256(payload).hexdigest())
        # 位图与 fixtures 里的基准图（固件自绘的比对对象）是同一张。
        golden = (mq.FIXTURE_DIR / "panel_plain.pbm").read_bytes()
        self.assertEqual(payload[8:], golden[len(b"P4\n400 300\n"):])

    def test_manifest_matches_the_payload(self):
        meta = self.save()
        text = (self.out / "version.txt").read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        self.assertLess(len(text), 320)           # 固件清单缓冲上限
        manifest = render_brief.parse_manifest(text)
        self.assertEqual(manifest["manifest"], "1")
        self.assertEqual(manifest["sha256"], meta["sha256"])
        self.assertEqual(manifest["pages"], "1")
        self.assertEqual(manifest["bytes"], "15008")
        self.assertEqual(render_brief.parse_manifest("garbage"), {})

    def test_meta_describes_every_row(self):
        self.save()
        meta = json.loads((self.out / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["panel"], "market")
        self.assertEqual(meta["live_rows"], 12)
        self.assertEqual(meta["total_rows"], 12)
        self.assertEqual(meta["as_of"], "2026-11-14 06:15:59")
        self.assertEqual(meta["generated_at"], "2026-11-14 06:20:00")
        self.assertEqual(meta["layout"], render_brief.LAYOUT_VERSION)
        self.assertTrue(meta["layout_validated"])
        self.assertFalse(meta["demo"])
        self.assertEqual([row["key"] for row in meta["rows"]],
                         [spec.key for spec in mq.PANEL_ROWS])
        self.assertEqual(meta["rows"][0]["cells"], ["3,634.35", "-0.59%", "472亿", "7,706亿"])
        self.assertEqual(meta["rows"][3]["source"], mq.SOURCE_TENCENT)
        self.assertEqual(meta["rows"][8]["source"], mq.SOURCE_NAVER_KR)

    def test_unchanged_quotes_do_not_rewrite_files(self):
        first = self.save()
        self.assertFalse(first["unchanged"])
        names = self.names()
        self.assertEqual(names, ["latest.bin", "latest.png", "meta.json", "version.txt"])
        before = {name: (self.out / name).read_bytes() for name in names}
        # 之后再跑一次、行情没变：文件逐字节不变（meta 里的生成时间也不更新）。
        second = self.save(now=datetime(2026, 11, 14, 6, 25, tzinfo=mq.TZ_CN))
        self.assertTrue(second["unchanged"])
        self.assertEqual(before, {name: (self.out / name).read_bytes() for name in names})

    def test_changed_quotes_replace_the_payload(self):
        first = self.save()
        self.panel.rows[0].quote.last += 1.0
        second = self.save()
        self.assertFalse(second["unchanged"])
        self.assertNotEqual(first["sha256"], second["sha256"])
        manifest = render_brief.parse_manifest(
            (self.out / "version.txt").read_text(encoding="utf-8"))
        self.assertEqual(manifest["sha256"], second["sha256"])

    def test_a_missing_file_is_restored(self):
        self.save()
        (self.out / "version.txt").unlink()
        meta = self.save()
        self.assertFalse(meta["unchanged"])
        self.assertTrue((self.out / "version.txt").is_file())

    def test_without_preview_only_the_device_files_are_written(self):
        # 每分钟发布时不出 PNG：设备不读它，而它占每次提交体积的一半以上。
        first = self.save(preview=False)
        self.assertFalse(first["unchanged"])
        self.assertEqual(self.names(), ["latest.bin", "meta.json", "version.txt"])
        with_preview = Path(self.tmp.name) / "with-preview"
        with redirect_stdout(io.StringIO()):
            render_brief.save(render_brief.draw_panel(self.panel), with_preview,
                              render_brief.panel_meta(self.panel, self.now))
        for name in ("latest.bin", "version.txt", "meta.json"):
            self.assertEqual((self.out / name).read_bytes(), (with_preview / name).read_bytes(),
                             name)
        # 行情没变时照样什么都不写（不会因为没有 PNG 而每轮重写、每轮提交）。
        before = {name: (self.out / name).read_bytes() for name in self.names()}
        second = self.save(preview=False, now=datetime(2026, 11, 14, 6, 21, tzinfo=mq.TZ_CN))
        self.assertTrue(second["unchanged"])
        self.assertEqual(before, {name: (self.out / name).read_bytes() for name in self.names()})
        # 行情变了：三个文件更新，仍然没有 PNG。
        self.panel.rows[0].quote.last += 1.0
        third = self.save(preview=False)
        self.assertFalse(third["unchanged"])
        self.assertEqual(self.names(), ["latest.bin", "meta.json", "version.txt"])

    def test_without_preview_an_existing_png_is_removed(self):
        # 否则仓库里会留下一张与 latest.bin 对不上的旧预览。
        self.save()
        before = {name: (self.out / name).read_bytes()
                  for name in ("latest.bin", "meta.json", "version.txt")}
        meta = self.save(preview=False, now=datetime(2026, 11, 14, 6, 21, tzinfo=mq.TZ_CN))
        self.assertTrue(meta["unchanged"])
        self.assertEqual(self.names(), ["latest.bin", "meta.json", "version.txt"])
        self.assertEqual(before, {name: (self.out / name).read_bytes() for name in before})

    def test_preview_comes_back_when_requested_again(self):
        self.save(preview=False)
        meta = self.save()
        self.assertFalse(meta["unchanged"])
        self.assertEqual(self.names(), ["latest.bin", "latest.png", "meta.json", "version.txt"])
        with render_brief.Image.open(self.out / "latest.png") as image:
            self.assertEqual(image.size, (400, 300))

    def test_leftovers_of_the_old_multi_page_brief_are_removed(self):
        self.out.mkdir(parents=True)
        leftovers = ["page_01.png", "page_255.png", "latest_combined.png",
                     "latest_contact_sheet.png"]
        for name in leftovers:
            (self.out / name).write_bytes(b"old")
        (self.out / "README.txt").write_text("keep", encoding="utf-8")
        self.save()
        for name in leftovers:
            self.assertFalse((self.out / name).exists(), name)
        self.assertTrue((self.out / "README.txt").is_file())
        # 行情没变的那一轮也会清理（旧文件可能是之后才被放回来的）。
        (self.out / "page_02.png").write_bytes(b"old")
        self.assertTrue(self.save()["unchanged"])
        self.assertFalse((self.out / "page_02.png").exists())

    def test_invalid_layout_writes_nothing(self):
        sheet = render_brief.draw_panel(self.panel)
        sheet.text(render_brief.NAME_X, render_brief.row_y(3), "叠字")
        with self.assertRaises(render_brief.LayoutError):
            render_brief.save(sheet, self.out, {})
        self.assertFalse(self.out.exists())


class CommandLineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out"

    def test_demo_render(self):
        code, stdout, _ = run_main(["--demo", "--out-dir", str(self.out)])
        self.assertEqual(code, 0)
        self.assertIn("12/12 行有效", stdout)
        self.assertEqual((self.out / "latest.bin").stat().st_size, 15008)
        meta = json.loads((self.out / "meta.json").read_text(encoding="utf-8"))
        self.assertTrue(meta["demo"])

    def test_no_preview_option(self):
        code, _, _ = run_main(["--demo", "--out-dir", str(self.out)])
        self.assertEqual(code, 0)
        self.assertTrue((self.out / "latest.png").is_file())
        code, stdout, _ = run_main(["--demo", "--no-preview", "--out-dir", str(self.out)])
        self.assertEqual(code, 0)
        self.assertIn("已删除 PNG 预览", stdout)
        self.assertEqual(sorted(path.name for path in self.out.iterdir()),
                         ["latest.bin", "meta.json", "version.txt"])

    def test_demo_data_never_counts_as_live(self):
        code, _, stderr = run_main(["--demo", "--require-live", "--out-dir", str(self.out)])
        self.assertEqual(code, 3)
        self.assertIn("保留上一版", stderr)
        self.assertFalse(self.out.exists())

    def test_live_render_with_all_sources(self):
        with mock.patch.object(mq, "collect_panel", return_value=panel_from(*ALL_SOURCES)):
            code, _, _ = run_main(["--require-live", "--out-dir", str(self.out)])
        self.assertEqual(code, 0)
        self.assertEqual((self.out / "latest.bin").stat().st_size, 15008)

    def test_one_failed_auxiliary_source_still_updates(self):
        panel = panel_from(mq.SOURCE_TENCENT)   # 11 行有效
        with mock.patch.object(mq, "collect_panel", return_value=panel):
            code, _, _ = run_main(["--require-live", "--out-dir", str(self.out)])
        self.assertEqual(code, 0)

    def test_too_few_live_rows_keeps_the_previous_image(self):
        self.out.mkdir()
        (self.out / "latest.bin").write_bytes(b"previous")
        panel = panel_from(mq.SOURCE_SINA, mq.SOURCE_NAVER_KR, mq.SOURCE_NAVER_JP)
        with mock.patch.object(mq, "collect_panel", return_value=panel):
            code, _, stderr = run_main(["--require-live", "--out-dir", str(self.out)])
        self.assertEqual(code, 3)
        self.assertIn("6 < 9", stderr)
        self.assertEqual((self.out / "latest.bin").read_bytes(), b"previous")
        self.assertEqual([path.name for path in self.out.iterdir()], ["latest.bin"])

    def test_min_rows_can_be_tightened(self):
        panel = panel_from(mq.SOURCE_TENCENT)
        with mock.patch.object(mq, "collect_panel", return_value=panel):
            code, _, _ = run_main(["--require-live", "--min-rows", "12",
                                   "--out-dir", str(self.out)])
        self.assertEqual(code, 3)
        for bad in ("0", "13"):
            code, _, _ = run_main(["--demo", "--min-rows", bad, "--out-dir", str(self.out)])
            self.assertEqual(code, 2)

    def test_layout_failure_exits_5_without_output(self):
        original = render_brief.draw_panel

        def broken(panel, footer_right=""):
            sheet = original(panel, footer_right)
            sheet.text(render_brief.NAME_X, render_brief.row_y(0), "叠字")
            return sheet

        with mock.patch.object(render_brief, "draw_panel", broken):
            code, _, stderr = run_main(["--demo", "--out-dir", str(self.out)])
        self.assertEqual(code, 5)
        self.assertIn("排版预检未通过", stderr)
        self.assertFalse(self.out.exists())

    def test_generated_time_uses_beijing_timezone(self):
        now = render_brief.china_now()
        self.assertEqual(now.utcoffset().total_seconds(), 8 * 60 * 60)
        self.assertEqual(now.tzinfo.key, "Asia/Shanghai")


def read_firmware(relative: str) -> str:
    return (FIRMWARE / relative).read_text(encoding="utf-8")


def cpp_int_constants(source: str) -> dict:
    """提取 ``inline constexpr int kName = <整数或已知常量的加减>;``。"""
    values: dict = {}
    pattern = re.compile(r"inline constexpr (?:int|uint\d+_t|size_t) (k\w+) = ([^;]+);")
    for name, expression in pattern.findall(source):
        expression = expression.strip()
        if not re.fullmatch(r"[\w\s+\-*()]+", expression):
            continue
        try:
            values[name] = int(eval(expression, {"__builtins__": {}}, dict(values)))  # noqa: S307
        except Exception:  # noqa: BLE001 - 不是纯整数表达式的常量与本测试无关
            continue
    return values


def cpp_string_constants(source: str) -> dict:
    """提取 ``inline constexpr const char *kName = "…";``（支持相邻字面量拼接）。"""
    values: dict = {}
    pattern = re.compile(r'inline constexpr const char \*(k\w+) =((?:\s*"(?:[^"\\]|\\.)*")+);')
    for name, literals in pattern.findall(source):
        values[name] = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', literals))
    return values


def cpp_macro_strings(source: str) -> dict:
    """提取 ``#define NAME "…" "…"``（支持反斜杠续行与相邻字面量拼接）。"""
    values: dict = {}
    joined = source.replace("\\\n", " ")
    for name, rest in re.findall(r'^#define (\w+)((?:\s+"[^"\n]*")+)\s*$', joined, re.M):
        values[name] = "".join(re.findall(r'"([^"\n]*)"', rest))
    return values


class FirmwareParityTest(unittest.TestCase):
    """服务器渲染器与固件各写了一遍的东西必须逐项相同。"""

    def test_layout_constants_match_the_firmware(self):
        layout = cpp_int_constants(read_firmware("ui/ui_market_panel_layout.h"))
        expected = {
            "kWidth": render_brief.WIDTH,
            "kHeight": render_brief.HEIGHT,
            "kTextBaseline": render_brief.TEXT_BASELINE,
            "kInkTop": render_brief.INK_TOP,
            "kInkBottom": render_brief.INK_BOTTOM,
            "kHeaderBarBottom": render_brief.HEADER_BAR_BOTTOM,
            "kHeaderTextY": render_brief.HEADER_TEXT_Y,
            "kPageLeft": render_brief.PAGE_LEFT,
            "kPageRight": render_brief.PAGE_RIGHT,
            "kColumnHeaderY": render_brief.COLUMN_HEADER_Y,
            "kColumnRuleY": render_brief.COLUMN_RULE_Y,
            "kRowTop": render_brief.ROW_TOP,
            "kRowPitch": render_brief.ROW_PITCH,
            "kTagX0": render_brief.TAG_X0,
            "kTagX1": render_brief.TAG_X1,
            "kTagPadTop": render_brief.TAG_PAD_TOP,
            "kNameX": render_brief.NAME_X,
            "kPriceRight": render_brief.PRICE_RIGHT,
            "kPctRight": render_brief.PCT_RIGHT,
            "kVolumeRight": render_brief.VOLUME_RIGHT,
            "kTurnoverRight": render_brief.TURNOVER_RIGHT,
            "kFooterRuleY": render_brief.FOOTER_RULE_Y,
            "kFooterTextY": render_brief.FOOTER_TEXT_Y,
            "kSeparatorStep": 2,
        }
        for name, value in expected.items():
            self.assertEqual(layout.get(name), value, name)
        self.assertEqual(
            (layout["kFooterStatusLeft"], layout["kFooterTextY"],
             layout["kFooterStatusRight"], layout["kHeight"]),
            render_brief.DEVICE_OVERLAY_BOX)

    def test_fixed_texts_match_the_firmware(self):
        texts = cpp_string_constants(read_firmware("ui/ui_market_panel_layout.h"))
        self.assertEqual(texts["kTitleText"], render_brief.TITLE_TEXT)
        self.assertEqual(
            (texts["kColumnName"], texts["kColumnPrice"], texts["kColumnPct"],
             texts["kColumnVolume"], texts["kColumnTurnover"]),
            render_brief.COLUMN_TITLES)
        contract = cpp_string_constants(read_firmware("network/market_panel_contract.h"))
        self.assertEqual(contract["kEmptyCell"], mq.EMPTY_CELL)
        self.assertEqual(contract["kTwoMarketLabel"], mq.TWO_MARKET_LABEL)
        # 基准图里用到的两条状态文字就是固件页脚真正会写的文字。
        statuses = {status for _, _, status in fixtures_tool.PANEL_IMAGES if status}
        self.assertIn(texts["kStatusEveryMinute"], statuses)
        self.assertIn("12" + texts["kStatusStaleSuffix"], statuses)

    def test_rows_match_the_firmware(self):
        source = read_firmware("network/market_panel_contract.h")
        table = source[source.index("inline constexpr RowSpec kRows"):]
        table = table[:table.index("};")]
        rows = re.findall(r'\{"([^"]+)",\s*Market::k(\w+),\s*"([^"]+)"\}', table)
        markets = {"AShare": "A", "HongKong": "港", "UnitedStates": "美",
                   "Korea": "韩", "Japan": "日"}
        self.assertEqual(
            [(key, markets[market], name) for key, market, name in rows],
            [(spec.key, spec.market, spec.name) for spec in mq.PANEL_ROWS])
        labels = re.search(r"kMarketLabels\[kMarketCount\] = \{([^}]*)\}", source).group(1)
        self.assertEqual(tuple(re.findall(r'"([^"]+)"', labels)), mq.MARKET_ORDER)

    def test_thresholds_match_the_firmware(self):
        source = read_firmware("network/market_panel_contract.h")
        constants = cpp_int_constants(source)
        self.assertEqual(constants["kRowCount"], len(mq.PANEL_ROWS))
        self.assertEqual(constants["kMinLiveRows"], render_brief.DEFAULT_MIN_ROWS)
        self.assertEqual(constants["kRefreshSeconds"], 60)      # 每分钟更新一次

        def cpp_double(name: str) -> float:
            return float(re.search(
                rf"inline constexpr double {name} = ([0-9.e+-]+);", source).group(1))

        self.assertEqual(cpp_double("kMaxAbsPct"), mq.MAX_ABS_PCT)
        self.assertEqual(cpp_double("kN225SourceTolerance"), mq.N225_SOURCE_TOLERANCE)
        self.assertEqual(cpp_double("kMaxPriceValue"), mq.MAX_PRICE_VALUE)
        self.assertEqual(cpp_double("kMaxPctValue"), mq.MAX_PCT_VALUE)
        self.assertEqual(cpp_double("kMaxAmountValue"), mq.MAX_AMOUNT_VALUE)

    def test_quote_urls_match_the_firmware(self):
        macros = cpp_macro_strings(read_firmware("core/app_network_config.h"))
        self.assertEqual(macros["WEATHER_CLOCK_MARKET_TENCENT_URL"], mq.TENCENT_URL)
        self.assertEqual(macros["WEATHER_CLOCK_MARKET_SINA_HK_URL"], mq.SINA_HK_URL)
        self.assertEqual(macros["WEATHER_CLOCK_MARKET_SINA_REFERER"], mq.SINA_REFERER)
        self.assertEqual(macros["WEATHER_CLOCK_MARKET_NAVER_KR_URL"], mq.NAVER_KR_URL)
        self.assertEqual(macros["WEATHER_CLOCK_MARKET_NAVER_JP_URL"], mq.NAVER_JP_URL)
        service = read_firmware("network/market_panel_service.cpp")
        agent = re.search(r"kQuoteUserAgent =((?:\s*\"[^\"]*\")+);", service).group(1)
        self.assertEqual("".join(re.findall(r'"([^"]*)"', agent)), mq.USER_AGENT)
        # 设备的位图兜底地址指向本脚本写出的两个文件。
        self.assertTrue(macros["WEATHER_CLOCK_AIBRIEF_URL"].endswith("/aibrief/latest.bin"))
        self.assertTrue(
            macros["WEATHER_CLOCK_AIBRIEF_MANIFEST_URL"].endswith("/aibrief/version.txt"))

    def test_payload_fits_the_firmware_page_limit(self):
        constants = cpp_int_constants(read_firmware("network/aibrief_contract.h"))
        self.assertEqual(constants["kWidth"], render_brief.WIDTH)
        self.assertEqual(constants["kHeight"], render_brief.HEIGHT)
        self.assertGreaterEqual(constants["kMaxPages"], render_brief.PANEL_PAGES)
        self.assertEqual(render_brief.PANEL_PAGES, 1)

    def test_device_font_lists_cover_the_panel_text(self):
        """固件字库只收面板用到的字：服务器这边每一段文字都必须在清单里。"""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "gen_market_panel_fonts",
            ROOT / "RLCD_CLOCK" / "tools" / "gen_market_panel_fonts.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        ascii_chars = {chr(code) for code in range(0x20, 0x7F)}
        regular = set(generator.REGULAR_TEXT) | ascii_chars
        bold = set(generator.BOLD_TEXT)

        sheet = render_brief.Sheet()
        sheets = [
            render_brief.draw_panel(panel_from(*sources), status)
            for _, sources, status in fixtures_tool.PANEL_IMAGES
        ]
        for drawn in sheets:
            for run in drawn.text_runs:
                if run["bold"]:
                    self.assertLessEqual(set(run["text"]), bold, run["text"])
                    continue
                for numeric, segment in sheet._runs(run["text"]):
                    allowed = bold if numeric else regular
                    self.assertLessEqual(set(segment), allowed, run["text"])

        # 生成的字库文件与清单同步（Opts 注释里记着实际收录的码点）。
        for name, _font, ascii_range, text, _purpose in generator.FONTS:
            header = (FIRMWARE / "assets" / f"{name}.c").read_text(encoding="utf-8")[:6000]
            opts = re.search(r"-r (\S+)", header).group(1)
            expected = generator.codepoint_ranges(text)
            if ascii_range:
                expected = f"{ascii_range},{expected}"
            self.assertEqual(opts, expected, name)

    def test_cross_language_fixtures_are_current(self):
        """expected_cells / format_cases / time_cases / panel_*.pbm 由当前实现生成。

        固件主机测试读这些文件：这里保证它们没有过期，那边保证固件算出同样的结果。
        """
        self.assertEqual(fixtures_tool.stale_files(), [])


class WorkflowTest(unittest.TestCase):
    """发布循环本身的测试在 test_publish_loop.py；这里只管本文件依赖的触发条件。"""

    def setUp(self):
        self.workflow = (ROOT / ".github" / "workflows" / "aibrief.yml").read_text(
            encoding="utf-8")

    def test_parity_inputs_trigger_the_workflow(self):
        """测试会读的渲染器文件和固件文件改动时，工作流必须被触发。"""
        for path in ("device_brief/render_brief.py", "device_brief/market_quotes.py",
                     "device_brief/mono_text.py", "device_brief/fixtures_tool.py",
                     "device_brief/fixtures/**", "device_brief/fonts/**",
                     "RLCD_CLOCK/main/ui/ui_market_panel_layout.h",
                     "RLCD_CLOCK/main/network/market_panel_contract.h",
                     "RLCD_CLOCK/main/network/market_panel_service.cpp",
                     "RLCD_CLOCK/main/network/aibrief_contract.h",
                     "RLCD_CLOCK/main/core/app_network_config.h",
                     "RLCD_CLOCK/main/assets/market_panel_*.c",
                     "RLCD_CLOCK/tools/gen_market_panel_fonts.py"):
            self.assertIn(f'- "{path}"', self.workflow, path)

    def test_workflow_runs_this_test_file_with_pinned_dependencies(self):
        self.assertIn("python3 device_brief/test_render_brief.py", self.workflow)
        # 位图要与固件自绘逐像素相同：渲染依赖必须固定版本。
        installs = re.findall(r"pip install [^\n]*", self.workflow)
        self.assertTrue(installs)
        for line in installs:
            self.assertRegex(line, r"pillow==[\d.]+ freetype-py==[\d.]+")

    def test_removed_command_line_flags_are_gone(self):
        # 旧版多页简报的参数已经不存在。
        for stale in ("--require-fresh-market", "--min-live-sources", "--news-per-source"):
            self.assertNotIn(stale, self.workflow)


if __name__ == "__main__":
    unittest.main()
