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
    ("tx_main", "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006,sh000688,sh000300,hkHSI,hkHSTECH,hkHSCEI,usDJI,usIXIC,usINX,usNDX", {}),
    ("tx_guess", "https://qt.gtimg.cn/q=gzN225,gzKS11,gzKOSPI,gzTOPIX,gzTPX,gzKQ11,gzKOSDAQ,gzHSI,gzDJI,gzFTSE,gzGDAXI,gzTWII,gzSTI,jpN225,krKS11,s_gzN225,usN225,hkN225", {}),
    ("tx_http", "http://qt.gtimg.cn/q=sh000001,hkHSI,usDJI", {}),
    ("tx_simple", "https://qt.gtimg.cn/q=s_sh000001,s_sz399001,s_hkHSI,s_usDJI,s_usIXIC,s_usINX", {}),
    ("tx_noua", "https://qt.gtimg.cn/q=sh000001", {"User-Agent": "ESP32 HTTP Client/1.0"}),
    ("tx_sb_n225", sb("N225"), {}),
    ("tx_sb_rj", sb("日经225"), {}),
    ("tx_sb_kospi", sb("KOSPI"), {}),
    ("tx_sb_hg", sb("韩国综合"), {}),
    ("tx_sb_topix", sb("东证指数"), {}),
    ("tx_sb_kosdaq", sb("KOSDAQ"), {}),
    ("sina_all", "https://hq.sinajs.cn/list=s_sh000001,s_sz399001,s_sz399006,sh000001,rt_hkHSI,hkHSI,rt_hkHSTECH,gb_$dji,gb_$ixic,gb_$inx,gb_dji,b_NKY,b_KOSPI,b_TPX,b_KOSDAQ,znb_NKY,znb_KOSPI,znb_TPX,znb_KOSDAQ,int_nikkei,hf_NK", SINA_REF),
    ("sina_http", "http://hq.sinajs.cn/list=s_sh000001,b_NKY,b_KOSPI", SINA_REF),
    ("sina_noref", "https://hq.sinajs.cn/list=s_sh000001,b_NKY", {}),
    ("em_ulist", "https://push2.eastmoney.com/api/qt/ulist.np/get?" + EM_Q, {}),
    ("em_ulist_http", "http://push2.eastmoney.com/api/qt/ulist.np/get?" + EM_Q, {}),
    ("em_ulist_delay", "https://push2delay.eastmoney.com/api/qt/ulist.np/get?" + EM_Q, {}),
    ("em_ulist_82", "https://82.push2.eastmoney.com/api/qt/ulist.np/get?" + EM_Q, {}),
    ("em_one_n225", "https://push2.eastmoney.com/api/qt/stock/get?secid=100.N225&" + EM_ONE, {}),
    ("em_one_ks11", "https://push2.eastmoney.com/api/qt/stock/get?secid=100.KS11&" + EM_ONE, {}),
    ("em_one_hsi", "https://push2.eastmoney.com/api/qt/stock/get?secid=100.HSI&" + EM_ONE, {}),
    ("em_one_spx", "https://push2.eastmoney.com/api/qt/stock/get?secid=100.SPX&" + EM_ONE, {}),
    ("em_kline_n225", "https://push2his.eastmoney.com/api/qt/stock/kline/get?ut=%s&secid=100.N225&klt=101&fqt=0&end=20500101&lmt=3&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61" % EM_UT, {}),
    ("em_kline_sh", "https://push2his.eastmoney.com/api/qt/stock/kline/get?ut=%s&secid=1.000001&klt=101&fqt=0&end=20500101&lmt=3&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61" % EM_UT, {}),
    ("yh_chart_n225", "https://query1.finance.yahoo.com/v8/finance/chart/%5EN225?interval=1d&range=1d", {}),
    ("yh_chart_ks11", "https://query1.finance.yahoo.com/v8/finance/chart/%5EKS11?interval=1d&range=1d", {}),
    ("yh_chart_kq11", "https://query1.finance.yahoo.com/v8/finance/chart/%5EKQ11?interval=1d&range=1d", {}),
    ("yh_chart_topx", "https://query1.finance.yahoo.com/v8/finance/chart/%5ETOPX?interval=1d&range=1d", {}),
    ("yh_chart_gspc", "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?interval=1d&range=1d", {}),
    ("yh_chart_hsi", "https://query1.finance.yahoo.com/v8/finance/chart/%5EHSI?interval=1d&range=1d", {}),
    ("yh_chart_sse", "https://query1.finance.yahoo.com/v8/finance/chart/000001.SS?interval=1d&range=1d", {}),
    ("yh_spark7", "https://query1.finance.yahoo.com/v7/finance/spark?symbols=%s&range=1d&interval=1d" % YH, {}),
    ("yh_spark8", "https://query1.finance.yahoo.com/v8/finance/spark?symbols=%s&range=1d&interval=1d" % YH, {}),
    ("yh_quote7", "https://query2.finance.yahoo.com/v7/finance/quote?symbols=%5EN225,%5EKS11", {}),
    ("yh_chart_http", "http://query1.finance.yahoo.com/v8/finance/chart/%5EN225?interval=1d&range=1d", {}),
    ("nv_kr", "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI,KOSDAQ", {}),
    ("nv_world_n225", "https://polling.finance.naver.com/api/realtime/worldstock/index/.N225", {}),
    ("nv_api_n225", "https://api.stock.naver.com/index/.N225/basic", {}),
    ("nv_api_kospi", "https://m.stock.naver.com/api/index/KOSPI/basic", {}),
    ("stooq", "https://stooq.com/q/l/?s=%5Enkx+%5Ekospi+%5Etpx+%5Espx+%5Edji+%5Endq+%5Ehsi+%5Eshc&f=sd2t2ohlcv&h&e=csv", {}),
    ("nikkei", "https://indexes.nikkei.co.jp/nkave/get_real_data?idx=nk225", {}),
    ("cnbc", "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols=.N225%7C.KS11%7C.SPX%7C.DJI%7C.IXIC%7C.HSI%7C.SSEC&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json&events=1", {}),
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
            out["text"] = text[:3500]
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
