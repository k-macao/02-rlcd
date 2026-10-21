#!/usr/bin/env python3
"""本地假 PushPlus 服务：模拟 https://www.pushplus.plus/send，用于手动推送的联调测试。

用法：
    PORT=4181 RECORD_FILE=/tmp/pushplus_record.json python3 mock_pushplus.py
    # 推送成功后，POST 的请求体会被原样记录到 RECORD_FILE（即“假文件”），
    # 检查该文件内容即可确认 server.py 是否真的发出了推送。

可选环境变量：
    PORT          监听端口，默认 4181
    RECORD_FILE   推送记录写入的文件，默认 /tmp/pushplus_record.json
    MOCK_FAIL=1   模拟 PushPlus 拒绝（返回 code=400），用于测试失败分支
"""
import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", "4181"))
RECORD_FILE = os.environ.get("RECORD_FILE", "/tmp/pushplus_record.json")
MOCK_FAIL = os.environ.get("MOCK_FAIL") == "1"


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # 健康检查：仅用于确认假服务已启动。
        return self._send_json(200, {"code": 200, "msg": "mock alive"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        raw_text = raw.decode("utf-8", "replace")

        # 把收到的推送请求完整记录到“假文件”，供检查是否真的有内容发出。
        record = {
            "received_at": datetime.now().isoformat(timespec="seconds"),
            "path": self.path,
            "method": "POST",
            "content_type": self.headers.get("Content-Type"),
            "raw_body": raw_text,
            "payload": json.loads(raw_text) if raw_text else None,
        }
        Path(RECORD_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(RECORD_FILE).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if MOCK_FAIL:
            # 模拟 PushPlus 业务拒绝：HTTP 200 但业务码非 200。
            return self._send_json(200, {"code": 400, "msg": "token invalid (mock)"})
        return self._send_json(200, {"code": 200, "msg": "ok (mock)"})

    def log_message(self, *args):
        pass  # 静默，避免测试刷屏


if __name__ == "__main__":
    print(f"Mock PushPlus listening on 0.0.0.0:{PORT}, record -> {RECORD_FILE}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
