#!/usr/bin/env python3
"""sensitive.py 单元测试：归一化、合规语境、演示数据不误伤、闸门与环境变量。

零第三方依赖，直接运行：
    python3 test_sensitive.py
"""
import json
import os
import tempfile
import unittest

import sensitive
import sources


class ScanTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SKIP_SENSITIVE_CHECK", None)
        os.environ.pop("SENSITIVE_FORCE", None)
        os.environ.pop("SENSITIVE_LEXICON", None)
        sensitive.reset()

    def tearDown(self):
        os.environ.pop("SKIP_SENSITIVE_CHECK", None)
        os.environ.pop("SENSITIVE_FORCE", None)
        os.environ.pop("SENSITIVE_LEXICON", None)
        sensitive.reset()

    def _words(self, text):
        return [h["word"] for h in sensitive.scan(text)]

    def test_clean_finance_news_passes(self):
        for title in (
            "现货铂金上涨超过7%，报1,745.93美元/盎司",
            "淡水河谷CEO：伊朗战争造成诸多项目的速度放缓",
            "美国拟禁止进口中国新型数据中心设备，我使馆：敦促美停止抹黑中企并威胁制裁",
            "中国证监会严肃查处*ST卓然严重财务造假案件",
            "你经历过的最离谱的骗局是什么？",
            "SEC Charges Founder and His Two New Jersey-Based Companies in Alleged $16 Million Ponzi Scheme",
            "S. 4395: Terrorism Risk Insurance Program Reauthorization Act of 2026",
            "Presidential Determination on Major Drug Transit or Major Illicit Drug Producing Countries",
            "国务院办公厅关于进一步加强烟花爆竹全链条安全监管的意见",
            "FCA fines firm for failures in anti-money laundering controls",
        ):
            self.assertEqual(sensitive.scan(title), [], title)

    def test_gambling_hit_and_separator_variant(self):
        self.assertIn("网络赌场", self._words("推荐网络赌场开户送彩金"))
        # 插入空格 / 符号 / 零宽字符仍命中
        self.assertIn("网络赌场", self._words("推荐网 络 赌 场开户"))
        self.assertIn("网络赌场", self._words("推荐网*络*赌*场"))
        self.assertIn("网络赌场", self._words("推荐网\u200b络赌场"))

    def test_fullwidth_ascii(self):
        # 全角英文字母折成半角后再按单词边界匹配
        self.assertIn("pornhub", self._words("访问 Ｐｏｒｎｈｕｂ 下载"))

    def test_html_tags_do_not_hide_words(self):
        self.assertIn("网络赌场", self._words('<div class="td-t">推荐<span>网络</span>赌场</div>'))

    def test_enforcement_context_allows_news(self):
        # 官方打击 / 查处 / 反对 = 新闻报道，不拦截
        self.assertEqual(self._words("公安机关打击网络赌场，查处开户送彩金广告"), [])
        self.assertEqual(self._words("坚决反对台独分裂行径"), [])
        self.assertEqual(self._words("禁毒部门破获贩毒团伙"), [])
        self.assertEqual(self._words("扫黄打非查处色情网站"), [])

    def test_high_severity_ignores_weak_context(self):
        # 「风险」只放行中严重度；台独属于高严重度，不能靠「风险」蒙混
        hits = self._words("台独风险升温")
        self.assertIn("台独", hits)

    def test_ascii_word_boundary(self):
        self.assertEqual(self._words("The crisis in energy markets deepens"), [])
        self.assertIn("isis", self._words("isis released a new video"))
        self.assertEqual(self._words("This is isolation of the market"), [])

    def test_traditional_chinese(self):
        self.assertIn("網路賭場", self._words("推薦網路賭場註冊"))
        self.assertEqual(self._words("警方打擊網路賭場"), [])

    def test_national_security_phrase(self):
        self.assertTrue(any(h["category"] == "national_security"
                            for h in sensitive.scan("公然煽动颠覆国家政权")))
        # 法规用语出现在「依法打击」语境下视为报道
        self.assertEqual(self._words("依法打击颠覆国家政权活动"), [])


class FilterBriefTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SKIP_SENSITIVE_CHECK", None)
        os.environ.pop("SENSITIVE_FORCE", None)
        os.environ.pop("SENSITIVE_LEXICON", None)
        sensitive.reset()

    def tearDown(self):
        os.environ.pop("SKIP_SENSITIVE_CHECK", None)
        os.environ.pop("SENSITIVE_FORCE", None)
        os.environ.pop("SENSITIVE_LEXICON", None)
        sensitive.reset()

    def test_drops_violating_item_keeps_clean(self):
        brief = {
            "金十数据": [
                {"title": "现货铂金上涨超过7%", "url": "https://a"},
                {"title": "推荐网络赌场开户送彩金", "url": "https://b"},
                {"title": "央行将开展5000亿元买断式逆回购", "url": "https://c"},
            ]
        }
        clean, report = sensitive.filter_brief(brief)
        titles = [it["title"] for it in clean["金十数据"]]
        self.assertEqual(titles, ["现货铂金上涨超过7%", "央行将开展5000亿元买断式逆回购"])
        self.assertEqual(report["dropped_count"], 1)
        self.assertFalse(report["ok"])
        self.assertIn("赌博", report["categories"])
        self.assertEqual(report["dropped"][0]["source"], "金十数据")

    def test_keeps_enforcement_news(self):
        brief = {"财联社 电报": [{"title": "公安部打击网络赌博平台", "url": ""}]}
        clean, report = sensitive.filter_brief(brief)
        self.assertEqual(report["dropped_count"], 0)
        self.assertEqual(len(clean["财联社 电报"]), 1)

    def test_empty_and_blank_titles(self):
        clean, report = sensitive.filter_brief({"x": [{"title": "", "url": ""}], "y": []})
        self.assertEqual(report["dropped_count"], 0)
        self.assertEqual(clean["x"][0]["title"], "")
        self.assertEqual(clean["y"], [])

    def test_inspect_brief_does_not_mutate(self):
        brief = {"金十数据": [{"title": "推荐网络赌场", "url": ""}]}
        report = sensitive.inspect_brief(brief)
        self.assertEqual(report["dropped_count"], 1)
        self.assertEqual(brief["金十数据"][0]["title"], "推荐网络赌场")


class GateTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SKIP_SENSITIVE_CHECK", None)
        os.environ.pop("SENSITIVE_FORCE", None)
        os.environ.pop("SENSITIVE_LEXICON", None)
        sensitive.reset()

    def tearDown(self):
        os.environ.pop("SKIP_SENSITIVE_CHECK", None)
        os.environ.pop("SENSITIVE_FORCE", None)
        os.environ.pop("SENSITIVE_LEXICON", None)
        sensitive.reset()

    def test_clean_payload_passes(self):
        gate = sensitive.check_push("章鱼 AI·全景分析（量化分析）", "<div>现货铂金上涨</div>")
        self.assertTrue(gate["ok"], gate)
        self.assertIn("放行", gate["reason"])
        self.assertEqual(gate["hits"], [])

    def test_remaining_hit_blocks(self):
        gate = sensitive.check_push("每日简报", "<p>本文教你如何访问网络赌场</p>")
        self.assertFalse(gate["ok"])
        self.assertIn("取消推送", gate["reason"])
        self.assertTrue(gate["hits"])

    def test_title_hit_blocks(self):
        gate = sensitive.check_push("网络赌场推荐", "<p>hello</p>")
        self.assertFalse(gate["ok"])

    def test_skip_env(self):
        os.environ["SKIP_SENSITIVE_CHECK"] = "1"
        gate = sensitive.check_push("网络赌场推荐", "网络赌场")
        self.assertTrue(gate["ok"])
        self.assertTrue(gate.get("skipped"))

    def test_force_block_and_pass(self):
        os.environ["SENSITIVE_FORCE"] = "block"
        gate = sensitive.check_push("干净标题", "<p>干净正文</p>")
        self.assertFalse(gate["ok"])
        self.assertTrue(gate.get("forced"))
        os.environ["SENSITIVE_FORCE"] = "pass"
        gate = sensitive.check_push("网络赌场", "网络赌场")
        self.assertTrue(gate["ok"])
        self.assertTrue(gate.get("forced"))

    def test_extra_lexicon(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as fh:
            fh.write("# 自定义\n独角兽退市黑话\nblock:自定义颠覆词\n")
            path = fh.name
        try:
            os.environ["SENSITIVE_LEXICON"] = path
            sensitive.reset()
            self.assertIn("独角兽退市黑话", [h["word"] for h in sensitive.scan("小心独角兽退市黑话")])
            self.assertTrue(any(h["severity"] == "high"
                                for h in sensitive.scan("传播自定义颠覆词")))
        finally:
            os.unlink(path)

    def test_strip_html_unescapes(self):
        self.assertIn("网络赌场", sensitive.strip_html("<b>网络赌场</b>"))
        self.assertIn("<", sensitive.strip_html("a &lt; b"))


class DemoDataTest(unittest.TestCase):
    """内置演示数据与由其生成的推送 HTML 都不得被误拦，否则离线推送永远发不出去。"""

    def test_all_demo_titles_pass(self):
        blocked = []
        for name, titles in sources._DEMO.items():
            for title in titles:
                hits = sensitive.scan(title)
                if hits:
                    blocked.append((name, title, [h["word"] for h in hits]))
        self.assertEqual(blocked, [])

    def test_demo_html_passes_gate(self):
        brief = {name: sources._demo_items(name) for name in sources.SOURCES}
        clean, report = sensitive.filter_brief(brief)
        self.assertEqual(report["dropped_count"], 0, report)
        html = sources.build_html(
            clean,
            review=sources.analyze_ashare(market=sources._ASHARE_SNAPSHOT),
            hk_review=sources.analyze_hk(market=sources._HK_SNAPSHOT),
            us_review=sources.analyze_us(market=sources._US_SNAPSHOT),
        )
        gate = sensitive.check_push("章鱼 AI·全景分析（量化分析）", html)
        self.assertTrue(gate["ok"], json.dumps(gate, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
