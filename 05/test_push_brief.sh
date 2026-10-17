#!/usr/bin/env bash
# 验证 push_brief.py（GitHub Actions 推送脚本）→ 假 PushPlus → 假文件
set -u
cd /home/user/05
# 优先用虚拟环境里的 Python；沙箱/CI 无 .venv 时回退系统 Python。
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi

MOCK_PORT=$($PY -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()")
echo "假PushPlus端口: $MOCK_PORT"
rm -f pushplus_record.json

PORT=$MOCK_PORT RECORD_FILE=/home/user/05/pushplus_record.json $PY mock_pushplus.py >/tmp/mock2.log 2>&1 &
MOCK_PID=$!
sleep 0.6

echo "=== 1. 执行 push_brief.py（强制大盘数据为最新，走完整推送链路）==="
PUSHPLUS_TOKEN=fake-token-abc PUSHPLUS_API_URL="http://127.0.0.1:$MOCK_PORT/send" MARKET_FRESHNESS_FORCE=fresh $PY push_brief.py
echo "退出码: $?（期望 0）"

echo ""
echo "=== 2. 假文件内容 ==="
cat pushplus_record.json

echo ""
echo "=== 3. 大盘数据非最新 → 退出码 3 且不推送 ==="
rm -f pushplus_record.json
PUSHPLUS_TOKEN=fake-token-abc PUSHPLUS_API_URL="http://127.0.0.1:$MOCK_PORT/send" MARKET_FRESHNESS_FORCE=stale $PY push_brief.py
RC=$?
echo "退出码: $RC（期望 3）"
if [ -f pushplus_record.json ]; then
  echo "❌ 大盘数据非最新时不应生成假文件（不应推送）"
else
  echo "✅ 未生成假文件（已拦截推送）"
fi

echo ""
echo "=== 4. SKIP_MARKET_CHECK=1 跳过闸门（测试/应急通道）==="
PUSHPLUS_TOKEN=fake-token-abc PUSHPLUS_API_URL="http://127.0.0.1:$MOCK_PORT/send" SKIP_MARKET_CHECK=1 $PY push_brief.py
echo "退出码: $?（期望 0）"

echo ""
echo "=== 5. SENSITIVE_FORCE=block → 退出码 4 且不推送 ==="
rm -f pushplus_record.json
PUSHPLUS_TOKEN=fake-token-abc PUSHPLUS_API_URL="http://127.0.0.1:$MOCK_PORT/send" MARKET_FRESHNESS_FORCE=fresh SENSITIVE_FORCE=block $PY push_brief.py
RC=$?
echo "退出码: $RC（期望 4）"
if [ -f pushplus_record.json ]; then
  echo "❌ 敏感词检测未通过时不应生成假文件（不应推送）"
else
  echo "✅ 未生成假文件（已拦截推送）"
fi

echo ""
echo "=== 6. 未配 token 的分支（期望退出码 1）==="
env -u PUSHPLUS_TOKEN -u PUSHPLUS_API_URL -u SKIP_MARKET_CHECK -u MARKET_FRESHNESS_FORCE -u SKIP_SENSITIVE_CHECK -u SENSITIVE_FORCE $PY push_brief.py
echo "退出码: $?"

kill $MOCK_PID 2>/dev/null
wait 2>/dev/null
echo "(已清理)"
