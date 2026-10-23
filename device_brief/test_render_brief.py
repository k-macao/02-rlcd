#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_brief.py 排版回归测试（不联网，全部用本地样例数据）。

    python3 -m pip install pillow
    python3 device_brief/test_render_brief.py

回归重点（2026-10 大字版改版后立下的规矩）：
- 全页最小字号不得低于 10px、正文不得低于 13px。面板是 4.2" 400×300，
  旧版 7~10px 在实物上看不清，不许再靠缩字号塞内容；
- 内容最满的一天（四指数 + 成交/涨跌停 + 领涨领跌 + 三条快讯）也不溢出 300px；
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


class FontSizeFloorTest(unittest.TestCase):
    def test_named_sizes_respect_floor(self):
        self.assertGreaterEqual(render_brief.FONT_MIN, 10)
        self.assertGreaterEqual(render_brief.FONT_BODY, 13)
        self.assertGreaterEqual(render_brief.FONT_SMALL, 12)
        for name in ("FONT_TITLE", "FONT_SECTION", "FONT_BODY",
                     "FONT_SMALL", "FONT_META", "FONT_MIN"):
            self.assertGreaterEqual(getattr(render_brief, name),
                                    render_brief.FONT_MIN, name)

    def test_no_text_drawn_below_floor(self):
        sizes = []
        original = render_brief.Sheet.text

        def recording(self_sheet, x, y, text, size=11, bold=False,
                      fill=render_brief.BLACK):
            sizes.append(size)
            return original(self_sheet, x, y, text, size, bold, fill)

        with mock.patch.object(render_brief.Sheet, "text", recording):
            draw()
        self.assertTrue(sizes, "draw_sheet 没有画任何文字？")
        self.assertGreaterEqual(min(sizes), render_brief.FONT_MIN, sizes)


class TypographyTest(unittest.TestCase):
    def test_variable_font_uses_regular_weight(self):
        font = render_brief.Sheet(render_brief.FONT_PATH).font(13)
        try:
            instances = font.get_variation_names()
        except (AttributeError, OSError):
            self.skipTest("selected font is static or has no named variations")

        regular_instance = next(
            (name for name in instances
             if name in (b"Regular", "Regular")),
            None,
        )
        if regular_instance is None:
            self.skipTest("selected variable font has no Regular instance")
        self.assertEqual(font.getname()[1], "Regular")

    def test_all_text_uses_one_regular_font(self):
        sheet = render_brief.Sheet(render_brief.FONT_PATH)
        self.assertIs(sheet.font(13, bold=False), sheet.font(13, bold=True))
        self.assertEqual(sheet.measure("AI 12.3%", 13, bold=False),
                         sheet.measure("AI 12.3%", 13, bold=True))

        calls = []
        original = sheet.draw.text

        def recording(position, text, **kwargs):
            calls.append(kwargs)
            return original(position, text, **kwargs)

        with mock.patch.object(sheet.draw, "text", recording):
            sheet.text(0, 0, "标题 123", 14, bold=True)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("stroke_width", calls[0])
        self.assertIs(calls[0]["font"], sheet.font(14))


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
        line = render_brief.sector_line(self.sheet, FULL_SUMMARY, self.width)
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
    def test_every_page_has_real_number_and_passes_preflight(self):
        renderer = render_brief.draw_all_pages(multi_data(), NOW, 51, 69, False)
        renderer.validate()
        total = len(renderer.pages)
        self.assertGreater(total, 1)
        for index, sheet in enumerate(renderer.pages, 1):
            self.assertEqual(sheet.image.size, (400, 300))
            footer = [r["text"] for r in sheet.text_runs if r["region"] == "footer"]
            self.assertIn(f"{index:02d} / {total:02d}", footer)
            self.assertTrue(all(r["size"] >= render_brief.FONT_MIN
                                for r in sheet.text_runs))

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
        )
        self.assertEqual(rendered, " ".join(title.split()))
        self.assertTrue(rendered.endswith("final unique tail"))
        self.assertNotIn("…", rendered)

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
        self.assertEqual(len(renderer.pages), 4)
        rendered = "\n".join(
            run["text"] for sheet in renderer.pages for run in sheet.text_runs
        )
        for i in range(1, 41):
            self.assertIn(f"K{i:02d}", rendered)

    def test_page_budget_adapts_news_without_cutting_analysis(self):
        brief = {
            f"测试源{i:02d}": [
                {"title": f"来源{i:02d} 快讯{j}"} for j in range(3)
            ] for i in range(12)
        }
        data = multi_data(brief)
        data.update({"review_ashare": None, "review_hk": None, "review_us": None,
                     "heatmap": None, "policy": None})
        renderer = render_brief.render_with_page_budget(
            data, NOW, 0, len(brief), False, max_pages=8
        )
        self.assertEqual(len(renderer.pages), 8)
        self.assertEqual(renderer.layout_meta["news_per_source"], 1)
        self.assertEqual(renderer.layout_meta["news_included"], 12)
        self.assertEqual(renderer.layout_meta["news_omitted"], 24)
        self.assertEqual(renderer.pages[0].page_title, "AI 每日总结")

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


if __name__ == "__main__":
    unittest.main()
