#!/usr/bin/env python3
"""push_brief.py 推送闸门测试：大盘数据不是最新就不推（退出码 3）；
敏感词残留则退出码 4。

零第三方依赖，直接运行：
    python3 test_push_gate.py

思路：本地假 PushPlus（mock_pushplus.py）接收推送并写“假文件”，
把 sources 的大盘采集与新闻源全部替换为本地注入，验证：
- 大盘数据非最新 → 退出码 3，且假 PushPlus 收不到任何请求（真的没推）；
- 大盘数据最新   → 退出码 0，假文件生成且载荷正确；默认一对一（载荷不带群组编码 topic）；
- SKIP_MARKET_CHECK=1 → 跳过闸门照常推送（测试/应急通道）；
- 违规快讯被 filter_brief 剔除后仍可推送；HTML 残留敏感词 → 退出码 4；
- SKIP_SENSITIVE_CHECK / SENSITIVE_FORCE 与新鲜度闸门同一套环境变量风格。
"""
import json
import os
import tempfile
import unittest

import push_brief
import sources

from test_server import free_port, start_mock


def _demo_brief():
    """18 个源全部用内置演示数据，测试不依赖外网。"""
    return {name: sources._demo_items(name)[:2] for name in sources.SOURCES}


def _injected_freshness(ok: bool):
    """注入 (market, freshness)：market 用内置快照，freshness 按需标最新/非最新。"""
    market = dict(sources._ASHARE_SNAPSHOT)
    freshness = {
        "ok": ok,
        "reason": "测试注入：" + ("大盘数据为最新" if ok else "大盘数据非最新"),
        "market_date": market["date"],
        "source": market["source"],
        "latest_kline_date": market["date"],
        "expected_date": market["date"],
        "checked_at": "2026-08-31 20:00:00",
    }
    return market, freshness


class MarketGateTest(unittest.TestCase):
    def setUp(self):
        self._orig_collect_all = sources.collect_all
        self._orig_collect_market = sources.collect_market_for_push
        self._orig_build_html = sources.build_html
        self._orig_api_url = push_brief.API_URL
        # 推送模式是模块级常量（import 时绑定），测试期内固定为默认的一对一。
        self._orig_topic = push_brief.TOPIC
        push_brief.TOPIC = ""
        sources.collect_all = _demo_brief
        self._tmp = tempfile.TemporaryDirectory()
        self.record_file = os.path.join(self._tmp.name, "pushplus_record.json")
        os.environ["PUSHPLUS_TOKEN"] = "fake-token-gate"
        os.environ.pop("SKIP_MARKET_CHECK", None)
        os.environ.pop("MARKET_FRESHNESS_FORCE", None)
        os.environ.pop("SKIP_SENSITIVE_CHECK", None)
        os.environ.pop("SENSITIVE_FORCE", None)
        os.environ.pop("SENSITIVE_LEXICON", None)

    def tearDown(self):
        sources.collect_all = self._orig_collect_all
        sources.collect_market_for_push = self._orig_collect_market
        sources.build_html = self._orig_build_html
        push_brief.API_URL = self._orig_api_url
        push_brief.TOPIC = self._orig_topic
        self._tmp.cleanup()
        for key in ("PUSHPLUS_TOKEN", "PUSHPLUS_API_URL", "PUSHPLUS_TOPIC",
                    "SKIP_MARKET_CHECK", "MARKET_FRESHNESS_FORCE",
                    "SKIP_SENSITIVE_CHECK", "SENSITIVE_FORCE", "SENSITIVE_LEXICON"):
            os.environ.pop(key, None)

    def _push_once(self, fresh: bool) -> int:
        """注入大盘检查结果后执行一次 push_brief.main()，返回退出码。"""
        sources.collect_market_for_push = lambda: _injected_freshness(fresh)
        mock_port = free_port()
        mock = start_mock(mock_port, self.record_file)
        try:
            # API_URL 是模块级常量（import 时绑定），须直接改属性而非环境变量。
            push_brief.API_URL = f"http://127.0.0.1:{mock_port}/send"
            return push_brief.main()
        finally:
            mock.terminate()
            mock.wait(timeout=5)

    def test_stale_market_blocks_push(self):
        # 大盘数据非最新 → 退出码 3，且不发出任何推送请求（假文件不应生成）。
        rc = self._push_once(fresh=False)
        self.assertEqual(rc, 3)
        self.assertFalse(os.path.exists(self.record_file),
                         "大盘数据非最新时不应发出推送")

    def test_fresh_market_pushes(self):
        # 大盘数据最新 → 正常推送：假文件生成且载荷正确。
        rc = self._push_once(fresh=True)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(self.record_file), "推送似乎没有发出")
        with open(self.record_file, encoding="utf-8") as f:
            payload = json.load(f)["payload"]
        self.assertEqual(payload["token"], "fake-token-gate")
        # 默认一对一推送：载荷不带群组编码。
        self.assertNotIn("topic", payload)
        self.assertIn("章鱼", payload["title"])
        self.assertIn("AI 看盘", payload["content"])  # 看盘板块随简报一同推送
        self.assertIn("AI 板块机会", payload["content"])  # 板块机会清单随简报一同推送

    def test_skip_market_check_bypasses_gate(self):
        # SKIP_MARKET_CHECK=1（测试/应急）→ 即便非最新也照常推送。
        os.environ["SKIP_MARKET_CHECK"] = "1"
        rc = self._push_once(fresh=False)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(self.record_file))

    def test_sensitive_item_dropped_then_push(self):
        # 单条违规快讯被剔除后，其余干净内容照常推送，正文不再出现该标题。
        def brief_with_hit():
            data = _demo_brief()
            data["金十数据"] = (
                [{"title": "推荐网络赌场开户送彩金", "url": "https://bad.example"}]
                + data["金十数据"]
            )
            return data
        sources.collect_all = brief_with_hit
        rc = self._push_once(fresh=True)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(self.record_file))
        with open(self.record_file, encoding="utf-8") as f:
            payload = json.load(f)["payload"]
        self.assertNotIn("网络赌场", payload["content"])
        self.assertNotIn("开户送彩金", payload["content"])

    def test_sensitive_remaining_blocks_push(self):
        # 渲染后的 HTML 仍残留敏感词 → 退出码 4，不发出推送。
        orig = sources.build_html

        def poisoned(*args, **kwargs):
            return orig(*args, **kwargs) + "<p>颠覆国家政权</p>"

        sources.build_html = poisoned
        rc = self._push_once(fresh=True)
        self.assertEqual(rc, 4)
        self.assertFalse(os.path.exists(self.record_file),
                         "敏感词残留时不应发出推送")

    def test_sensitive_force_block(self):
        os.environ["SENSITIVE_FORCE"] = "block"
        rc = self._push_once(fresh=True)
        self.assertEqual(rc, 4)
        self.assertFalse(os.path.exists(self.record_file))

    def test_skip_sensitive_check_bypasses_gate(self):
        os.environ["SKIP_SENSITIVE_CHECK"] = "1"
        orig = sources.build_html

        def poisoned(*args, **kwargs):
            return orig(*args, **kwargs) + "<p>网络赌场</p>"

        sources.build_html = poisoned
        rc = self._push_once(fresh=True)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(self.record_file))


if __name__ == "__main__":
    unittest.main(verbosity=2)
