#!/usr/bin/env python3
"""临时探针：在 CI 里实测各行情接口的可达性与返回格式（用完即删）。"""
import json, sys, time
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
from urllib.parse import quote

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
EM_UT = "fa5fd1943c7b386f172d6893dbfba10b"
EM_FIELDS = "f1,f2,f3,f4,f5,f6,f12,f13,f14,f124"
EM_SECIDS = ("1.000001,0.399001,0.399006,1.000688,100.HSI,100.HSTECH,100.DJIA,100.SPX,"
             "100.NDX,100.N225,100.KS11,100.KOSPI200,100.TOPIX,100.KQ11")
EM_Q = f"fltt=2&invt=2&ut={EM_UT}&fields={EM_FIELDS}&secids={EM_SECIDS}"
EM_ONE = "ut=%s&fltt=2&invt=2&fields=f43,f44,f45,f46,f47,f48,f57,f58,f60,f86,f169,f170" % EM_UT
YH = "%5EN225,%5EKS11,%5EKQ11,%5EGSPC,%5EDJI,%5EIXIC,%5EHSI,000001.SS,399001.SZ"
SINA_REF = {"Referer": "https://finance.sina.com.cn/"}

def sb(q):
    return "https://smartbox.gtimg.cn/s3/?v=2&t=all&q=" + quote(q)

PROBES = [
    ("tx_panel", "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006,hkHSI,hkHSTECH,usDJI,usIXIC,usINX,gzN225,gzKS11,gzKOSDAQ", {}),
    ("tx_panel_http", "http://qt.gtimg.cn/q=sh000001,sz399001,sz399006,hkHSI,hkHSTECH,usDJI,usIXIC,usINX,gzN225,gzKS11,gzKOSDAQ", {"User-Agent": "ESP32 HTTP Client/1.0"}),
    ("tx_rhk", "https://qt.gtimg.cn/q=r_hkHSI,r_hkHSTECH,s_r_hkHSI", {}),
    ("tx_gz_more", "https://qt.gtimg.cn/q=gzN225,gzKS11,gzKOSDAQ,gzTPX,gzKS200,gzKOSPI200,gzTOPX,gzJPX400,gzN300,gzNKY", {}),
    ("nv_kr", "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI,KOSDAQ", {}),
    ("nv_kr_espua", "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI,KOSDAQ", {"User-Agent": "ESP32 HTTP Client/1.0"}),
    ("nv_kr_http", "http://polling.finance.naver.com/api/realtime/domestic/index/KOSPI,KOSDAQ", {}),
    ("nv_world_n225", "https://polling.finance.naver.com/api/realtime/worldstock/index/.N225", {}),
    ("nv_world_n225_espua", "https://polling.finance.naver.com/api/realtime/worldstock/index/.N225", {"User-Agent": "ESP32 HTTP Client/1.0"}),
    ("nv_world_multi", "https://polling.finance.naver.com/api/realtime/worldstock/index/.N225,.TOPX,.KS11,.HSI,.DJI,.IXIC,.INX,.SSEC", {}),
    ("nv_world_topx", "https://polling.finance.naver.com/api/realtime/worldstock/index/.TOPX", {}),
    ("nv_world_tpx", "https://polling.finance.naver.com/api/realtime/worldstock/index/.TPX", {}),
    ("nv_nation_jpn", "https://api.stock.naver.com/index/nation/JPN", {}),
    ("nv_mix", "https://polling.finance.naver.com/api/realtime?query=SERVICE_INDEX:KOSPI,KOSDAQ%7CSERVICE_WORLD_INDEX:.N225", {}),
    ("em_hstech", "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&ut=%s&fields=f2,f3,f4,f5,f6,f12,f13,f14,f124&secids=124.HSTECH,100.HSTECH,100.KOSDAQ,100.KQ11,100.TPX,100.TOPX,100.N225,100.KS11,100.HSI" % EM_UT, {}),
    ("em_ulist_espua", "https://push2.eastmoney.com/api/qt/ulist.np/get?" + EM_Q, {"User-Agent": "ESP32 HTTP Client/1.0"}),
    ("sina_hk", "https://hq.sinajs.cn/list=rt_hkHSI,rt_hkHSTECH", SINA_REF),
]


def fetch(item):
    name, url, extra = item
    headers = {"User-Agent": UA, "Accept": "*/*"}
    headers.update(extra)
    started = time.time()
    out = {"name": name, "url": url}
    try:
        with urlopen(Request(url, headers=headers), timeout=12) as resp:
            raw = resp.read(200000)
            out["status"] = resp.status
            out["content_type"] = resp.headers.get("Content-Type", "")
            out["bytes"] = len(raw)
            try:
                text = raw.decode("utf-8")
                out["encoding"] = "utf-8"
            except UnicodeDecodeError:
                text = raw.decode("gbk", "replace")
                out["encoding"] = "gbk"
            out["text"] = text[:6000]
            import base64
            out["b64"] = base64.b64encode(raw[:8000]).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"[:300]
        body = getattr(exc, "read", None)
        if body:
            try:
                out["error_body"] = body().decode("utf-8", "replace")[:400]
            except Exception:  # noqa: BLE001
                pass
    out["elapsed"] = round(time.time() - started, 2)
    return out


def main():
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch, PROBES))
    doc = {"at_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "results": results}
    json.dump(doc, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
