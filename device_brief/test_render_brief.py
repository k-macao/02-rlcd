#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_brief.py 排版回归测试（不联网，全部用本地样例数据）。

    python3 -m pip install pillow freetype-py
    python3 device_brief/test_render_brief.py

回归重点（2026-10 大字版改版后立下的规矩）：
- 全页文字统一为阿里巴巴普惠体 16px，标题/数值串使用 Bold，其余文字使用 Regular；
  面板是 4.2" 400×300，不许再靠缩字号塞内容；
- 文字必须经 FreeType 原生 MONO 在整数像素绘制，页面只允许纯黑/纯白，禁止灰阶；
- 内容最满的一天（四指数 + 成交/涨跌停 + 领涨领跌 + 三条快讯）也不溢出 300px；
- 快讯默认全量推送：48 页页位用满、各源均衡，只有放不下时才截断且不裁剪分析栏目；
- 没有行情 / 没有快讯 / 没有板块信号时同样能渲染；
- 输出仍是固件约定的 15008 字节 AIB1 负载（改排版不能动协议）。
"""
from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import render_brief  # noqa: E402

NOW = datetime(2026, 10, 17, 11, 17)

FULL_SUMMARY = {
    "bias": "中性", "bull": 41, "bear": 59, "signals": 105,
    "headline": "市场分歧加大，美联储与AI 算力轮动频繁，多空力量接近，方向尚待明朗。",
    "sectors_up": ["美联储", "消费", "AI 算力"],
    "sectors_down": ["地缘", "贵金属"],
    "opportunity_sectors": [
        {"tag": "美联储", "net": 3, "sources": 22},
        {"tag": "消费", "net": 1, "sources": 13},
    ],
    "pressure_sectors": [{"tag": "地缘", "net": -9, "sources": 20}],
    "flow": "资金净流入集中在美联储、消费；回避地缘",
}

FULL_REVIEW = {
    "date": "2026-10-16", "source": "tencent",
    "indices": [
        {"name": "上证指数", "close": 3715.28, "pct": 0.24},
        {"name": "深证成指", "close": 11602.46, "pct": -0.12},
        {"name": "创业板指", "close": 2772.36, "pct": -0.39},
        {"name": "科创50", "close": 1358.55, "pct": -0.96},
    ],
    "turnover": {"amount": 12345},
    "breadth": {"limit_up": 61, "limit_down": 10},
    "leaders": [{"name": "汽车整车"}, {"name": "食品饮料"}],
    "laggards": [{"name": "贵金属"}],
}

HEADLINES = [
    "Chinese premier Li Qiang attended the opening ceremony of the 2026 "
    "World Traditional Medicine Congress and delivered remarks, Xinhua reported.",
    "央行开展 3800 亿元逆回购操作 维护流动性合理充裕",
    "NBU Governor Pyshnyi said Russia's unwarranted aggression must stop.",
]


def draw(summary=FULL_SUMMARY, review=FULL_REVIEW, headlines=HEADLINES,
         demo=False):
    return render_brief.draw_sheet(summary, None, review, headlines,
                                   NOW, 51, 69, demo)


def lowest_ink_row(sheet) -> int:
    """图上最靠下的一行黑色像素（0 起）。"""
    img = sheet.image
    for y in range(img.height - 1, -1, -1):
        if img.crop((0, y, img.width, y + 1)).getextrema()[0] < 128:
            return y
    return -1


class FontSize16Test(unittest.TestCase):
    def test_named_sizes_are_16px(self):
        for name in ("FONT_TITLE", "FONT_SECTION", "FONT_BODY",
                     "FONT_SMALL", "FONT_META", "FONT_MIN"):
            self.assertEqual(getattr(render_brief, name), 16, name)

    def test_all_drawn_text_is_16px(self):
        sizes = []
        original = render_brief.Sheet.text

        def recording(self_sheet, x, y, text, size=16, bold=False,
                      fill=render_brief.BLACK):
            sizes.append(size)
            return original(self_sheet, x, y, text, size, bold, fill)

        with mock.patch.object(render_brief.Sheet, "text", recording):
            draw()
        self.assertTrue(sizes, "draw_sheet 没有画任何文字？")
        self.assertEqual(set(sizes), {16}, sizes)


class TypographyTest(unittest.TestCase):
    def test_alibaba_regular_and_bold_files(self):
        sheet = render_brief.Sheet()
        regular = sheet.font(16)
        bold = sheet.font(16, bold=True)
        self.assertEqual(regular.getname(), ("Alibaba PuHuiTi 3.0", "55 Regular"))
        self.assertEqual(bold.getname(), ("Alibaba PuHuiTi 3.0", "85 Bold"))
        self.assertIsNot(regular, bold)
        self.assertIs(sheet._font(True, 16), bold)

    def test_digits_and_titles_bold_and_other_text_regular(self):
        sheet = render_brief.Sheet()
        regular = sheet.font(16)
        bold = sheet.font(16, bold=True)
        self.assertEqual(list(sheet._runs("涨幅 +12.5%")),
                         [(False, "涨幅 "), (True, "+12.5%")])

        calls = []
        original = render_brief.MonoFont.render

        def recording(self_font, draw, x, y, text, fill):
            calls.append((self_font, text, x, y, fill))
            return original(self_font, draw, x, y, text, fill)

        with mock.patch.object(render_brief.MonoFont, "render", recording):
            sheet.text(0, 0, "正文 123", 16)
            sheet.text(0, 20, "标题 123", 16, bold=True)
        self.assertEqual(len(calls), 4)
        self.assertIs(calls[0][0], regular)
        self.assertIs(calls[1][0], bold)
        self.assertIs(calls[2][0], bold)
        self.assertIs(calls[3][0], bold)
        self.assertTrue(all(isinstance(call[2], int) and isinstance(call[3], int)
                            for call in calls))

    def test_native_mono_pixels_and_shared_metrics(self):
        sheet = render_brief.Sheet()
        font = sheet.font(16)
        text = "阅读清晰 AI 123"
        layout = font.layout(text)
        self.assertEqual(font.measure(text), layout.advance)
        self.assertTrue(all(isinstance(p.x, int) and isinstance(p.y, int)
                            for p in layout.placements))
        self.assertTrue(all(p.glyph.image.mode == "1" for p in layout.placements))
        sheet.text(12, 48, text, 16)
        self.assertEqual(sum(sheet.image.histogram()[1:255]), 0)
        self.assertEqual(sheet.measure(text, 16), layout.advance)


class LayoutTest(unittest.TestCase):
    def test_fullest_day_fits_and_fills_page(self):
        sheet = draw()
        bottom = lowest_ink_row(sheet)
        self.assertLessEqual(bottom, render_brief.HEIGHT - 4)
        # 也不能只画半页就收工（快讯区被误删之类的回归）。
        self.assertGreaterEqual(bottom, 250)

    def test_missing_market_headlines_and_sectors(self):
        summary = {"bias": "中性", "bull": 50, "signals": 0, "headline": ""}
        sheet = draw(summary=summary, review=None, headlines=[])
        self.assertLessEqual(lowest_ink_row(sheet), render_brief.HEIGHT - 4)

    def test_partial_indices_and_demo_flag(self):
        review = {"date": "2026-10-16", "source": "eastmoney",
                  "indices": FULL_REVIEW["indices"][:1]}
        sheet = draw(review=review, demo=True)
        self.assertLessEqual(lowest_ink_row(sheet), render_brief.HEIGHT - 4)


class SectorLineTest(unittest.TestCase):
    def setUp(self):
        self.sheet = render_brief.Sheet(render_brief.FONT_PATH)
        self.width = 346

    def test_net_and_sources_shown_when_they_fit(self):
        summary = {
            "opportunity_sectors": [FULL_SUMMARY["opportunity_sectors"][0]],
            "pressure_sectors": FULL_SUMMARY["pressure_sectors"],
        }
        line = render_brief.sector_line(self.sheet, summary, self.width)
        self.assertIn("美联储+3·22源", line)
        self.assertIn("地缘-9·20源", line)

    def test_long_tags_degrade_without_overflowing(self):
        summary = {
            "opportunity_sectors": [
                {"tag": "AI 算力", "net": 12, "sources": 38},
                {"tag": "半导体", "net": 8, "sources": 31},
            ],
            "pressure_sectors": [{"tag": "贵金属", "net": -14, "sources": 29}],
        }
        line = render_brief.sector_line(self.sheet, summary, self.width)
        self.assertLessEqual(
            self.sheet.measure(line, render_brief.FONT_BODY), self.width)

    def test_fallbacks(self):
        plain = {"sectors_up": ["消费"], "sectors_down": ["地产"]}
        self.assertIn("利好 消费",
                      render_brief.sector_line(self.sheet, plain, self.width))
        self.assertEqual(
            render_brief.sector_line(self.sheet, {}, self.width), "暂无明确方向")


class WrapAndPayloadTest(unittest.TestCase):
    def test_english_headline_uses_pixel_width_not_char_count(self):
        sheet = render_brief.Sheet(render_brief.FONT_PATH)
        line = sheet.wrap(HEADLINES[0], render_brief.FONT_SMALL,
                          render_brief.CONTENT_W - 10, 1)[0]
        # 旧版按 38 个字符硬截；按像素折行后英文一行能装下更多。
        self.assertGreater(len(line), 38)
        self.assertLessEqual(
            sheet.measure(line, render_brief.FONT_SMALL),
            render_brief.CONTENT_W - 10)

    def test_numeric_tokens_are_not_split_between_lines(self):
        sheet = render_brief.Sheet(render_brief.FONT_PATH)
        text = "成交额 3,172 亿"
        lines = sheet.wrap_all(text, 16, 100)
        self.assertEqual("".join(lines), text)
        self.assertTrue(any("3,172" in line for line in lines), lines)
        self.assertTrue(all("3," not in line or "3,172" in line for line in lines), lines)
        date_lines = sheet.wrap_all("日期 2026-10-24 后", 16, 90)
        self.assertTrue(any("2026-10-24" in line for line in date_lines), date_lines)

    def test_payload_contract_unchanged(self):
        sheet = draw()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            sheet.save(out, {"generated_at": "test"})
            payload = (out / "latest.bin").read_bytes()
            self.assertEqual(len(payload), 15008)
            self.assertEqual(payload[:4], b"AIB1")
            self.assertEqual(struct.unpack("<HH", payload[4:8]), (400, 300))
            meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["bytes"], 15008)
            self.assertTrue((out / "latest.png").exists())


# ---------------------------------------------------------------- 多页杂志版回归
def multi_data(brief=None):
    heatmap = {
        "headline": "测试热力图",
        "keywords": [
            {"display": "AI", "heat": 88, "net": 3, "sentiment_label": "偏多",
             "sources": 4, "mentions": 9,
             "etf_pair": [{"code": "03438.HK"}, {"code": "03140.HK"}]},
            {"display": "黄金", "heat": 42, "net": -1, "sentiment_label": "偏空",
             "sources": 2, "mentions": 3,
             "etf_pair": [{"code": "02840.HK"}, {"code": "03566.HK"}]},
        ],
    }
    policy = {
        "headline": "政策信号保持中性，等待后续确认。", "stance": "中性",
        "buckets": [{"tag": "央行 · 流动性", "net": 1, "sources": 3,
                     "mentions": 5, "stance": "偏鸽"}],
    }
    return {
        "summary": FULL_SUMMARY,
        "review_ashare": FULL_REVIEW,
        "review_hk": FULL_REVIEW,
        "review_us": FULL_REVIEW,
        "heatmap": heatmap,
        "policy": policy,
        "brief": brief if brief is not None else {
            "测试源": [{"title": title} for title in HEADLINES],
        },
    }


def manual_renderer(page_count):
    renderer = render_brief.MultiPageRenderer(render_brief.FONT_PATH)
    for index in range(page_count):
        sheet = renderer.new_page("排版校样", "TEST")
        sheet.text(render_brief.PAGE_MARGIN, render_brief.BODY_TOP,
                   f"第 {index + 1} 页正文", render_brief.FONT_BODY)
        renderer.add_page(sheet)
    renderer.finalize(NOW)
    return renderer


class MultiPageMagazineTest(unittest.TestCase):
    def test_every_page_has_read_time_and_passes_preflight(self):
        renderer = render_brief.draw_all_pages(multi_data(), NOW, 51, 69, False)
        renderer.validate()
        total = len(renderer.pages)
        self.assertGreater(total, 1)
        for sheet in renderer.pages:
            self.assertEqual(sheet.image.size, (400, 300))
            self.assertTrue(any(r["region"] == "body" and r["text"].strip()
                                for r in sheet.text_runs))
            footer = [r["text"] for r in sheet.text_runs if r["region"] == "footer"]
            self.assertIn("读取 11:17", footer)
            # 页码已取消：页脚不得再出现 "NN / MM"。
            self.assertFalse(any(" / " in text for text in footer))
            self.assertTrue(all(r["size"] == 16 for r in sheet.text_runs))
            title_run = next(r for r in sheet.text_runs
                             if r["region"] == "header" and r["text"] == sheet.page_title)
            self.assertTrue(title_run["bold"])

    def test_long_english_headline_is_fully_paginated(self):
        title = "Market outlook " + "alpha beta gamma delta " * 80 + "final unique tail"
        renderer = render_brief.MultiPageRenderer(render_brief.FONT_PATH)
        render_brief.draw_news_pages(renderer, {"测试源": [{"title": title}]}, 1, 0)
        renderer.finalize(NOW)
        renderer.validate()
        self.assertGreater(len(renderer.pages), 1)
        rendered = "".join(
            run["text"] for sheet in renderer.pages for run in sheet.text_runs
            if run["region"] == "body" and run["size"] == render_brief.FONT_SMALL
            and run["text"] != "001"
        )
        self.assertEqual(rendered, " ".join(title.split()))
        self.assertTrue(rendered.endswith("final unique tail"))
        self.assertNotIn("…", rendered)
        title_runs = [run for sheet in renderer.pages for run in sheet.text_runs
                      if run["region"] == "body" and run["size"] == render_brief.FONT_SMALL
                      and run["text"] != "001"]
        self.assertTrue(title_runs)
        self.assertTrue(all(run["bold"] for run in title_runs))
        for sheet in renderer.pages:
            body_x = [run["bbox"][0] for run in sheet.text_runs
                      if run["region"] == "body" and run["size"] == render_brief.FONT_SMALL]
            self.assertTrue(any(x < 200 for x in body_x))
            self.assertTrue(any(x >= 200 for x in body_x), "超长标题末页不应只剩单侧一栏")

    def test_all_forty_heat_keywords_are_kept(self):
        data = multi_data()
        data["heatmap"]["keywords"] = [
            {"display": f"K{i:02d}", "heat": i, "net": 0,
             "sentiment_label": "中性", "etf_pair": [{"code": "03000.HK"}]}
            for i in range(1, 41)
        ]
        renderer = render_brief.MultiPageRenderer(render_brief.FONT_PATH)
        render_brief.draw_heatmap_pages(renderer, data["heatmap"], 1, 0)
        renderer.finalize(NOW)
        renderer.validate()
        self.assertEqual(len(renderer.pages), 5)
        rendered = "\n".join(
            run["text"] for sheet in renderer.pages for run in sheet.text_runs
        )
        for i in range(1, 41):
            self.assertIn(f"K{i:02d}", rendered)

    def _news_brief(self, source_count=12, per_source=3):
        return {
            f"测试源{i:02d}": [
                {"title": f"来源{i:02d} 快讯{j}"} for j in range(per_source)
            ] for i in range(source_count)
        }

    def _news_only_data(self, brief):
        data = multi_data(brief)
        data.update({"review_ashare": None, "review_hk": None, "review_us": None,
                     "heatmap": None, "policy": None})
        return data

    def test_full_news_pushes_every_title_when_budget_allows(self):
        brief = self._news_brief(source_count=6, per_source=4)
        data = self._news_only_data(brief)
        renderer = render_brief.render_with_page_budget(
            data, NOW, 0, len(brief), False, max_pages=48
        )
        meta = renderer.layout_meta
        self.assertTrue(meta["news_full"])
        self.assertEqual(meta["news_mode"], "full")
        self.assertEqual(meta["news_available"], 24)
        self.assertEqual(meta["news_included"], 24)
        self.assertEqual(meta["news_omitted"], 0)
        rendered = "\n".join(run["text"] for sheet in renderer.pages
                              for run in sheet.text_runs)
        for i in range(6):
            for j in range(4):
                self.assertIn(f"来源{i:02d} 快讯{j}", rendered)
        self.assertIn("全量", rendered)

    def test_page_budget_fills_news_up_to_firmware_limit(self):
        brief = self._news_brief()
        data = self._news_only_data(brief)
        renderer = render_brief.render_with_page_budget(
            data, NOW, 0, len(brief), False, max_pages=8
        )
        meta = renderer.layout_meta
        # 页位用满：正好 8 页，且收录条数多于旧的「每源 1 条」口径（12 条）。
        self.assertEqual(len(renderer.pages), 8)
        self.assertEqual(meta["news_available"], 36)
        self.assertGreater(meta["news_included"], 12)
        self.assertEqual(meta["news_omitted"], 36 - meta["news_included"])
        # 各源均衡：纳入条数最多的源与最少的源相差不超过 1 条。
        counts = []
        rendered = [run["text"] for sheet in renderer.pages for run in sheet.text_runs]
        for i in range(12):
            counts.append(sum(1 for text in rendered
                              if text.startswith(f"来源{i:02d} 快讯")))
        self.assertLessEqual(max(counts) - min(counts), 1, counts)
        self.assertEqual(renderer.pages[0].page_title, "AI 每日总结")

    def test_news_pool_is_source_balanced(self):
        brief = self._news_brief(source_count=3, per_source=3)
        pool = [title for _label, title in render_brief._news_pool(brief)]
        self.assertEqual(pool[:3], ["来源00 快讯0", "来源01 快讯0", "来源02 快讯0"])
        self.assertEqual(pool[3:6], ["来源00 快讯1", "来源01 快讯1", "来源02 快讯1"])
        self.assertEqual(len(pool), 9)
        self.assertEqual(len(render_brief._group_news(brief, 2)["财经快讯"]), 6)

    def test_explicit_news_quota_still_supported(self):
        brief = self._news_brief()
        data = self._news_only_data(brief)
        renderer = render_brief.render_with_page_budget(
            data, NOW, 0, len(brief), False, max_pages=48, news_per_source=2
        )
        self.assertEqual(renderer.layout_meta["news_per_source"], 2)
        self.assertEqual(renderer.layout_meta["news_included"], 24)
        self.assertFalse(renderer.layout_meta["news_full"])

    def test_demo_collection_is_fully_offline(self):
        with mock.patch.object(render_brief.sources, "collect_all",
                               side_effect=AssertionError("demo 不应联网")):
            data = render_brief.collect_brief_002(demo=True)
        self.assertEqual(len(data["brief"]), 69)
        self.assertEqual(data["review_ashare"]["source"], "snapshot")

    def test_save_removes_stale_pages_and_writes_contact_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            manual_renderer(2).save(out, {"test": True})
            self.assertTrue((out / "page_02.png").exists())
            manual_renderer(1).save(out, {"test": True})
            self.assertFalse((out / "page_02.png").exists())
            self.assertTrue((out / "latest_contact_sheet.png").exists())
            self.assertEqual(len((out / "latest.bin").read_bytes()), 15008)
            meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["pages"], 1)
            self.assertTrue(meta["layout_validated"])

    def test_overlap_blocks_save_before_creating_output(self):
        renderer = manual_renderer(1)
        sheet = renderer.pages[0]
        sheet.text(render_brief.PAGE_MARGIN, render_brief.BODY_TOP,
                   "叠压文字", render_brief.FONT_BODY)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "must-not-exist"
            with self.assertRaises(render_brief.LayoutError):
                renderer.save(out, {})
            self.assertFalse(out.exists())

    def test_gray_pixels_block_save(self):
        renderer = manual_renderer(1)
        renderer.pages[0].image.putpixel((0, 0), 128)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "must-not-exist"
            with self.assertRaisesRegex(render_brief.LayoutError, "灰阶像素"):
                renderer.save(out, {})
            self.assertFalse(out.exists())


class FreshnessAndScheduleTest(unittest.TestCase):
    def test_live_collection_reuses_market_freshness_result(self):
        market = {
            "date": "2026-10-23", "source": "eastmoney", "indices": [],
            "turnover": {}, "breadth": {}, "leaders": [], "laggards": [],
        }
        freshness = {"ok": True, "source": "eastmoney", "reason": "测试：最新"}
        with (mock.patch.object(render_brief.sources, "collect_all", return_value={}),
              mock.patch.object(render_brief.sources, "analyze_brief", return_value={}),
              mock.patch.object(render_brief.sources, "collect_market_for_push",
                                return_value=(market, freshness)) as collect_market,
              mock.patch.object(render_brief.sources, "get_ashare_market",
                                side_effect=AssertionError("不应重复抓取 A 股日 K")),
              mock.patch.object(render_brief.sources, "get_hk_market", return_value={}),
              mock.patch.object(render_brief.sources, "get_us_market", return_value={}),
              mock.patch.object(render_brief.sources, "analyze_keyword_heatmap", return_value={}),
              mock.patch.object(render_brief.sources, "analyze_policy", return_value={})):
            data = render_brief.collect_brief_002()
        collect_market.assert_called_once_with()
        self.assertIs(data["market_ashare"], market)
        self.assertIs(data["market_freshness"], freshness)

    def test_stale_market_blocks_render_and_output(self):
        data = multi_data({"live-source": [{"title": "unique live headline"}]})
        data["market_freshness"] = {
            "ok": False, "source": "snapshot", "reason": "测试：旧快照",
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "must-not-exist"
            argv = ["render_brief.py", "--require-fresh-market", "--out-dir", str(out)]
            with (mock.patch.object(render_brief, "collect_brief_002", return_value=data),
                  mock.patch.object(render_brief, "render_with_page_budget") as render,
                  mock.patch.object(sys, "argv", argv)):
                self.assertEqual(render_brief.main(), 3)
            render.assert_not_called()
            self.assertFalse(out.exists())

    def test_schedule_is_twice_daily_and_requires_freshness(self):
        workflow = (render_brief.ROOT / ".github" / "workflows" / "aibrief.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(workflow.count("- cron:"), 2)
        self.assertIn('- cron: "10 23 * * *"', workflow)  # 北京 07:10
        self.assertIn('- cron: "10 11 * * *"', workflow)  # 北京 19:10
        self.assertIn("--require-fresh-market", workflow)

    def test_generated_time_uses_beijing_timezone(self):
        now = render_brief.china_now()
        self.assertEqual(now.utcoffset().total_seconds(), 8 * 60 * 60)
        self.assertEqual(now.tzinfo.key, "Asia/Shanghai")


if __name__ == "__main__":
    unittest.main()
