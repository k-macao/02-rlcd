#!/usr/bin/env python3
"""PROBE（临时）：把四个行情接口的原始响应和港股的几种取法存下来，合并前删除。"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "device_brief"))
import market_quotes as mq  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
targets = {
    "tencent_quotes.txt": (mq.TENCENT_URL, {}),
    "sina_hk.txt": (mq.SINA_HK_URL, {"Referer": mq.SINA_REFERER}),
    "naver_kr.json": (mq.NAVER_KR_URL, {}),
    "naver_jp.json": (mq.NAVER_JP_URL, {}),
    # 港股：腾讯不带 r_ 的代码疑似延迟 15 分钟，比较几种取法的行情时间
    "variant_tencent_hk.txt": ("https://qt.gtimg.cn/q=hkHSI,hkHSTECH,r_hkHSI,r_hkHSTECH", {}),
    "variant_sina_hk.txt": ("https://hq.sinajs.cn/list=rt_hkHSI,rt_hkHSTECH,hkHSI,hkHSTECH",
                            {"Referer": mq.SINA_REFERER}),
    "variant_tencent_topix.txt": ("https://qt.gtimg.cn/q=gzTPX,gzTOPIX,gzN225,r_hkHSI", {}),
}
lines = [f"fetched_at_utc={datetime.now(timezone.utc).isoformat(timespec='seconds')}"]
for name, (url, headers) in targets.items():
    merged = {"User-Agent": mq.USER_AGENT, "Accept": "*/*"}
    merged.update(headers)
    try:
        with urlopen(Request(url, headers=merged), timeout=10) as response:
            body = response.read(262144)
            (out / name).write_bytes(body)
            lines.append(f"{name}: HTTP {response.status}, {len(body)} bytes")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"{name}: FAILED {type(exc).__name__}: {exc}")
(out / "capture.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
