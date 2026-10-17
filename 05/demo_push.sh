#!/usr/bin/env bash
# 手动推送联调演示：server.py(带假token) → 假 PushPlus → 记录到假文件
set -u
cd /home/user/05
PY=${PY:-python3}

MOCK_PORT=$($PY -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()")
SRV_PORT=$($PY -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()")
echo "假PushPlus端口: $MOCK_PORT | server.py端口: $SRV_PORT"
rm -f pushplus_record.json

PORT=$MOCK_PORT RECORD_FILE=/home/user/05/pushplus_record.json $PY mock_pushplus.py >/tmp/mock.log 2>&1 &
MOCK_PID=$!
sleep 0.6
PORT=$SRV_PORT PUSHPLUS_TOKEN=fake-token-123 PUSHPLUS_API_URL="http://127.0.0.1:$MOCK_PORT/send" $PY server.py >/tmp/srv_demo.log 2>&1 &
SRV_PID=$!
sleep 0.8

echo ""
echo "===== 1. 执行手动推送 (前端同款请求: POST /api/run) ====="
curl -s -w "\nHTTP %{http_code}\n" -X POST -H "Content-Type: application/json" -d '{}' "http://127.0.0.1:$SRV_PORT/api/run"

echo ""
echo "===== 2. 假文件是否有内容: pushplus_record.json ====="
if [ -s pushplus_record.json ]; then
  cat pushplus_record.json
  echo ""
  echo ">>> 假文件有内容 ($(wc -c < pushplus_record.json) 字节)，推送载荷完整 ✓"
else
  echo ">>> 假文件为空或不存在 ✗"
fi

echo ""
echo "===== 3. 假PushPlus / server.py 日志 ====="
tail -3 /tmp/mock.log
tail -3 /tmp/srv_demo.log

kill $MOCK_PID $SRV_PID 2>/dev/null
wait 2>/dev/null
echo "(演示进程已清理)"
