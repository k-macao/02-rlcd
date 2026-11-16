#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行情数据面板的数据层：直连公开行情接口，只取「最新数据」。

面板固定 12 行，按用户给定的市场顺序排列：A、港、美、韩、日。
每行只有四个数：最新点位、涨跌幅、成交量（股）、成交额（本币）。

数据源（2026-11-15 在 GitHub Actions 上逐个实测，原始响应留档在 fixtures/）：

  腾讯证券 qt.gtimg.cn        A 股 / 港股 / 美股指数：点位、涨跌幅、成交量、成交额；
                              日韩指数（gz 前缀）只有点位与涨跌幅，没有成交量。
  新浪财经 hq.sinajs.cn       港股指数的成交股数（腾讯港股指数只给成交额），需带 Referer。
  Naver 金融 polling API      KOSPI / KOSDAQ 实时点位 + 成交量 + 成交额；
                              日经 225 / TOPIX 点位 + 成交量（东京行情延迟 15 分钟）。

固件 `RLCD_CLOCK/main/network/market_panel_contract.h` 用同一套接口、同一套
合并规则和同一套数字格式直接在设备上每分钟刷新；两边共用 fixtures/ 做回归，
改这里的解析或格式时必须同步改固件侧并跑两边的测试。

本模块零第三方依赖，解析函数全部是纯函数（文本进、字典出），联网只在
``collect_panel()`` 里发生，便于离线测试。
"""
from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

TZ_CN = ZoneInfo("Asia/Shanghai")
TZ_HK = ZoneInfo("Asia/Hong_Kong")
TZ_US = ZoneInfo("America/New_York")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
FETCH_TIMEOUT = 8
FETCH_ATTEMPTS = 2

# 与固件 app_network_config.h 里的四个地址逐字一致。
# 港股指数必须用带 r_ 前缀的实时代码：不带前缀的 hkHSI / hkHSTECH 延迟 15 分钟
# （2026-11-16 盘中实测：同一次请求里 hkHSI 的行情时间是 09:45:16，r_hkHSI 是 10:00:21）。
TENCENT_URL = (
    "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006,r_hkHSI,r_hkHSTECH,"
    "usDJI,usIXIC,usINX,gzN225,gzKS11,gzKOSDAQ"
)
# 腾讯实时港股代码的前缀；行键不带它（hkHSI / hkHSTECH），与新浪一致。
TENCENT_REALTIME_PREFIX = "r_"
SINA_HK_URL = "https://hq.sinajs.cn/list=rt_hkHSI,rt_hkHSTECH"
SINA_REFERER = "https://finance.sina.com.cn/"
NAVER_KR_URL = "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI,KOSDAQ"
NAVER_JP_URL = "https://polling.finance.naver.com/api/realtime/worldstock/index/.N225,.TOPX"

SOURCE_TENCENT = "tencent"
SOURCE_SINA = "sina"
SOURCE_NAVER_KR = "naver_kr"
SOURCE_NAVER_JP = "naver_jp"
SOURCE_LABELS = {
    SOURCE_TENCENT: "腾讯证券",
    SOURCE_SINA: "新浪财经",
    SOURCE_NAVER_KR: "Naver 金融（韩国）",
    SOURCE_NAVER_JP: "Naver 金融（日本）",
}

# 日经 225：腾讯是实时点位，Naver 延迟 15 分钟但收盘后给的是官方收盘价。
# 盘中两者相差超过这个比例，说明腾讯的环球指数源异常（实测 gzTPX 就是坏数据），改信 Naver。
N225_SOURCE_TOLERANCE = 0.03
# 指数单日涨跌幅超过这个值按脏数据丢弃。
MAX_ABS_PCT = 50.0


@dataclass(frozen=True)
class RowSpec:
    key: str      # 行唯一键（同时是腾讯 / Naver 的代码；港股在腾讯的代码是 r_ + 行键）
    market: str   # 市场标签：A / 港 / 美 / 韩 / 日
    name: str     # 面板上显示的名称


# 行顺序即显示顺序；固件 market_panel::kRows 必须与此逐行一致。
PANEL_ROWS = (
    RowSpec("sh000001", "A", "上证指数"),
    RowSpec("sz399001", "A", "深证成指"),
    RowSpec("sz399006", "A", "创业板指"),
    RowSpec("hkHSI", "港", "恒生指数"),
    RowSpec("hkHSTECH", "港", "恒生科技"),
    RowSpec("usDJI", "美", "道琼斯"),
    RowSpec("usIXIC", "美", "纳斯达克"),
    RowSpec("usINX", "美", "标普500"),
    RowSpec("KOSPI", "韩", "KOSPI"),
    RowSpec("KOSDAQ", "韩", "KOSDAQ"),
    RowSpec("N225", "日", "日经225"),
    RowSpec("TOPX", "日", "东证指数"),
)
MARKET_ORDER = ("A", "港", "美", "韩", "日")
# 「两市成交额」= 沪市 + 深市（上证指数与深证成指的成交额）。
TWO_MARKET_KEYS = ("sh000001", "sz399001")


@dataclass
class Quote:
    last: Optional[float] = None
    pct: Optional[float] = None
    volume: Optional[float] = None     # 成交量，单位：股
    turnover: Optional[float] = None   # 成交额，单位：本币元
    time: Optional[datetime] = None    # 行情时间（带时区）
    status: str = ""                   # Naver 的 OPEN / CLOSE
    source: str = ""

    @property
    def valid(self) -> bool:
        return self.last is not None


@dataclass
class PanelRow:
    spec: RowSpec
    quote: Quote = field(default_factory=Quote)

    @property
    def valid(self) -> bool:
        return self.quote.valid


@dataclass
class PanelData:
    rows: List[PanelRow]
    sources: Dict[str, bool]
    demo: bool = False

    @property
    def live_rows(self) -> int:
        return sum(1 for row in self.rows if row.valid)

    @property
    def as_of(self) -> Optional[datetime]:
        """全表最新一笔行情的时间；休市期间它不会变，位图因此保持不变。"""
        times = [row.quote.time for row in self.rows if row.valid and row.quote.time]
        return max(times) if times else None

    def two_market_turnover(self) -> Optional[float]:
        by_key = {row.spec.key: row.quote for row in self.rows}
        values = [by_key[key].turnover for key in TWO_MARKET_KEYS if key in by_key]
        if len(values) != len(TWO_MARKET_KEYS) or any(v is None for v in values):
            return None
        return float(sum(values))


# ---------------------------------------------------------------- 数字格式（固件侧逐条对应）
_DECIMAL = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)")


def _to_float(text) -> Optional[float]:
    """只接受纯十进制写法（可带千分位逗号），与固件 parse_number 的口径一致。"""
    if text is None:
        return None
    cleaned = str(text).replace(",", "").strip(" \t")
    if not _DECIMAL.fullmatch(cleaned):
        return None
    value = float(cleaned)
    return value if math.isfinite(value) else None


def _positive(value: Optional[float]) -> Optional[float]:
    return value if value is not None and value > 0 else None


def _clean_pct(value: Optional[float]) -> Optional[float]:
    if value is None or abs(value) > MAX_ABS_PCT:
        return None
    return value


# 超出这些上限的数值不可能是真实行情，直接显示为空（固件侧同值，保证定长缓冲区放得下）。
MAX_PRICE_VALUE = 1e9
MAX_PCT_VALUE = 1e6
MAX_AMOUNT_VALUE = 1e18


def fmt_price(value: Optional[float]) -> str:
    """点位：千分位 + 两位小数，例如 61,142.35。"""
    if value is None or not math.isfinite(value) or value <= 0 or value >= MAX_PRICE_VALUE:
        return "--"
    return f"{value:,.2f}"


def fmt_pct(value: Optional[float]) -> str:
    """涨跌幅：始终带正负号，四舍五入后为零时写 0.00%。"""
    if value is None or not math.isfinite(value) or abs(value) >= MAX_PCT_VALUE:
        return "--"
    body = f"{abs(value):.2f}"
    if float(body) == 0:
        return "0.00%"
    return ("+" if value > 0 else "-") + body + "%"


def _fmt_scaled(scaled: float, unit: str) -> str:
    if scaled < 9.995:
        return f"{scaled:.2f}{unit}"
    if scaled < 99.95:
        return f"{scaled:.1f}{unit}"
    return f"{scaled:,.0f}{unit}"


def fmt_cn_number(value: Optional[float]) -> str:
    """成交量 / 成交额：按 万 / 亿 / 万亿 取三到四位有效数字，例如 472亿、1.66万亿。"""
    if value is None or not math.isfinite(value) or value <= 0 or value >= MAX_AMOUNT_VALUE:
        return "--"
    if value >= 9999.5e8:
        return _fmt_scaled(value / 1e12, "万亿")
    if value >= 9999.5e4:
        return _fmt_scaled(value / 1e8, "亿")
    if value >= 9999.5:
        return _fmt_scaled(value / 1e4, "万")
    return f"{value:,.0f}"


EMPTY_CELL = "--"
TWO_MARKET_LABEL = "两市成交"


def fmt_as_of(moment: Optional[datetime]) -> str:
    """页眉右侧的行情时间：北京时间 ``MM-DD HH:MM``；未知时写 ``--``。"""
    if moment is None:
        return EMPTY_CELL
    return moment.astimezone(TZ_CN).strftime("%m-%d %H:%M")


def two_market_text(turnover: Optional[float]) -> str:
    """页脚左侧的沪深两市成交额。"""
    return f"{TWO_MARKET_LABEL} {fmt_cn_number(turnover)}"


# ---------------------------------------------------------------- 解析：腾讯证券
_TENCENT_LINE = re.compile(r'v_([A-Za-z0-9_.]+)="([^"]*)"')


def _parse_time(text: str, fmt: str, tz: ZoneInfo) -> Optional[datetime]:
    """交易所当地时间 → 带时区的时刻；1970 年以前或格式不对都按未知处理。"""
    try:
        moment = datetime.strptime(text.strip(), fmt).replace(tzinfo=tz)
    except (ValueError, AttributeError):
        return None
    return moment if moment.year >= 1970 else None


def _parse_iso_time(text) -> Optional[datetime]:
    """ISO 8601 带时区的时刻（Naver localTradedAt）；不带时区无法定位绝对时刻，按未知处理。"""
    try:
        moment = datetime.fromisoformat(str(text or "").strip())
    except ValueError:
        return None
    if moment.tzinfo is None or moment.year < 1970:
        return None
    return moment


def parse_tencent(text: str) -> Dict[str, Quote]:
    """腾讯证券 ``v_代码="字段~字段~…";``，按 ``~`` 切分。

    沪深 / 港 / 美（长格式，至少 38 个字段）：
        [3] 最新  [30] 时间  [32] 涨跌幅%  [36] 成交量  [37] 成交额
      沪深指数 [36] 单位手、[37] 单位万元；
      港股指数 [36][37] 都是成交额（万港元），没有成交股数；只认实时代码
      ``r_hkHSI`` / ``r_hkHSTECH``（不带 ``r_`` 的延迟 15 分钟），返回的键去掉 ``r_``；
      美股指数 [36] 是成交股数，[37] 是「股数×点位」的无意义值，丢弃。
    环球指数（gz 前缀，至少 6 个字段）：
        [2] 北京时间  [3] 最新  [5] 涨跌幅%（没有成交量）
    """
    out: Dict[str, Quote] = {}
    for symbol, payload in _TENCENT_LINE.findall(text or ""):
        fields = payload.split("~")
        quote = Quote(source=SOURCE_TENCENT)
        if symbol.startswith("gz"):
            if len(fields) < 6:
                continue
            quote.last = _positive(_to_float(fields[3]))
            quote.pct = _clean_pct(_to_float(fields[5]))
            quote.time = _parse_time(fields[2], "%Y-%m-%d %H:%M:%S", TZ_CN)
        else:
            if len(fields) < 38:
                continue
            quote.last = _positive(_to_float(fields[3]))
            quote.pct = _clean_pct(_to_float(fields[32]))
            volume = _positive(_to_float(fields[36]))
            amount = _positive(_to_float(fields[37]))
            if symbol.startswith(("sh", "sz")):
                quote.volume = volume * 100 if volume else None      # 手 → 股
                quote.turnover = amount * 1e4 if amount else None    # 万元 → 元
                quote.time = _parse_time(fields[30], "%Y%m%d%H%M%S", TZ_CN)
            elif symbol.startswith(TENCENT_REALTIME_PREFIX + "hk"):
                quote.turnover = amount * 1e4 if amount else None    # 万港元 → 港元
                quote.time = _parse_time(fields[30], "%Y/%m/%d %H:%M:%S", TZ_HK)
                symbol = symbol[len(TENCENT_REALTIME_PREFIX):]
            elif symbol.startswith("us"):
                quote.volume = volume
                quote.time = _parse_time(fields[30], "%Y-%m-%d %H:%M:%S", TZ_US)
            else:
                continue
        if quote.last is None or quote.pct is None:
            continue
        out[symbol] = quote
    return out


# ---------------------------------------------------------------- 解析：新浪港股
_SINA_LINE = re.compile(r'hq_str_([A-Za-z0-9_$]+)="([^"]*)"')


def parse_sina_hk(text: str) -> Dict[str, Quote]:
    """新浪 ``var hq_str_rt_hkHSI="HSI,恒生指数,…";``，按逗号切分。

        [6] 最新  [8] 涨跌幅%  [11] 成交额（千港元）  [12] 成交量（股）
        [17] 日期  [18] 时间（香港时间）
    返回的键与腾讯一致（hkHSI / hkHSTECH），便于合并。
    """
    out: Dict[str, Quote] = {}
    for symbol, payload in _SINA_LINE.findall(text or ""):
        if not symbol.startswith("rt_hk"):
            continue
        fields = payload.split(",")
        if len(fields) < 19:
            continue
        quote = Quote(source=SOURCE_SINA)
        quote.last = _positive(_to_float(fields[6]))
        quote.pct = _clean_pct(_to_float(fields[8]))
        amount = _positive(_to_float(fields[11]))
        quote.turnover = amount * 1e3 if amount else None
        quote.volume = _positive(_to_float(fields[12]))
        quote.time = _parse_time(f"{fields[17]} {fields[18]}", "%Y/%m/%d %H:%M:%S", TZ_HK)
        if quote.last is None or quote.pct is None:
            continue
        out[symbol[len("rt_"):]] = quote
    return out


# ---------------------------------------------------------------- 解析：Naver 金融
def parse_naver(text: str) -> Dict[str, Quote]:
    """Naver polling JSON：``datas`` 里每项一个指数，只取带 Raw 后缀的纯数字字段。

    韩国指数用 itemCode（KOSPI / KOSDAQ），环球指数用 reutersCode（.N225 / .TOPX，去掉点）。
    成交额只对韩国指数可信：日经的成交额字段单位标注有误、TOPIX 为空，一律不用。
    """
    out: Dict[str, Quote] = {}
    try:
        doc = json.loads(text or "")
    except (TypeError, ValueError):
        return out
    items = doc.get("datas") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        domestic = bool(item.get("itemCode"))
        code = str(item.get("itemCode") or item.get("reutersCode") or "").lstrip(".")
        if not code:
            continue
        quote = Quote(source=SOURCE_NAVER_KR if domestic else SOURCE_NAVER_JP)
        quote.last = _positive(_to_float(item.get("closePriceRaw")))
        quote.pct = _clean_pct(_to_float(item.get("fluctuationsRatioRaw")))
        quote.volume = _positive(_to_float(item.get("accumulatedTradingVolumeRaw")))
        if domestic:
            quote.turnover = _positive(_to_float(item.get("accumulatedTradingValueRaw")))
        quote.status = str(item.get("marketStatus") or "")
        quote.time = _parse_iso_time(item.get("localTradedAt"))
        if quote.last is None or quote.pct is None:
            continue
        out[code] = quote
    return out


# ---------------------------------------------------------------- 合并（固件 merge_sources 同规则）
def _merge_hk(tencent: Optional[Quote], sina: Optional[Quote]) -> Quote:
    """港股：腾讯给点位 / 涨跌幅 / 成交额，新浪补成交股数；腾讯缺行时整行用新浪。"""
    if tencent is None:
        return sina or Quote()
    merged = Quote(**vars(tencent))
    if sina is not None:
        merged.volume = sina.volume
        if merged.turnover is None:
            merged.turnover = sina.turnover
    return merged


def _merge_kr(naver: Optional[Quote], tencent_gz: Optional[Quote]) -> Quote:
    """韩国：Naver 是交易所实时行情且带成交量；Naver 失败时腾讯只能给点位。"""
    if naver is not None:
        return naver
    return tencent_gz or Quote()


def _merge_n225(naver: Optional[Quote], tencent_gz: Optional[Quote]) -> Quote:
    """日经 225：盘中用腾讯实时点位、Naver 成交量；收盘后用 Naver 官方收盘价。"""
    if naver is None:
        return tencent_gz or Quote()
    if tencent_gz is None or naver.status == "CLOSE":
        return naver
    if abs(tencent_gz.last - naver.last) > naver.last * N225_SOURCE_TOLERANCE:
        return naver
    merged = Quote(**vars(tencent_gz))
    merged.volume = naver.volume
    merged.status = naver.status
    return merged


def merge_panel(tencent: Dict[str, Quote], sina: Dict[str, Quote],
                naver_kr: Dict[str, Quote], naver_jp: Dict[str, Quote]) -> List[PanelRow]:
    rows: List[PanelRow] = []
    for spec in PANEL_ROWS:
        key = spec.key
        if spec.market in ("A", "美"):
            quote = tencent.get(key) or Quote()
        elif spec.market == "港":
            quote = _merge_hk(tencent.get(key), sina.get(key))
        elif key == "KOSPI":
            quote = _merge_kr(naver_kr.get(key), tencent.get("gzKS11"))
        elif key == "KOSDAQ":
            quote = _merge_kr(naver_kr.get(key), tencent.get("gzKOSDAQ"))
        elif key == "N225":
            quote = _merge_n225(naver_jp.get(key), tencent.get("gzN225"))
        else:  # TOPX：腾讯 / 新浪的 TOPIX 是坏数据，只信 Naver
            quote = naver_jp.get(key) or Quote()
        rows.append(PanelRow(spec, quote))
    return rows


# ---------------------------------------------------------------- 抓取
def _decode(raw: bytes, encoding: str) -> str:
    return raw.decode(encoding, "replace")


def fetch_text(url: str, encoding: str = "utf-8", headers: Optional[dict] = None,
               timeout: int = FETCH_TIMEOUT, attempts: int = FETCH_ATTEMPTS) -> str:
    """GET 一个公开行情接口；失败重试一次，仍失败则抛出最后一次异常。"""
    merged = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    merged.update(headers or {})
    last_error: Optional[Exception] = None
    for _ in range(max(1, attempts)):
        try:
            with urlopen(Request(url, headers=merged), timeout=timeout) as response:
                return _decode(response.read(262144), encoding)
        except Exception as exc:  # noqa: BLE001 - 任何网络错误都只影响这一个源
            last_error = exc
    raise last_error if last_error else RuntimeError("fetch failed")


# 每个源：(源标识, 地址, 编码, 额外请求头, 解析函数)
SOURCES = (
    (SOURCE_TENCENT, TENCENT_URL, "gbk", None, parse_tencent),
    (SOURCE_SINA, SINA_HK_URL, "gb18030", {"Referer": SINA_REFERER}, parse_sina_hk),
    (SOURCE_NAVER_KR, NAVER_KR_URL, "utf-8", None, parse_naver),
    (SOURCE_NAVER_JP, NAVER_JP_URL, "utf-8", None, parse_naver),
)
FIXTURES = {
    SOURCE_TENCENT: ("tencent_quotes.txt", "gbk"),
    SOURCE_SINA: ("sina_hk.txt", "gb18030"),
    SOURCE_NAVER_KR: ("naver_kr.json", "utf-8"),
    SOURCE_NAVER_JP: ("naver_jp.json", "utf-8"),
}


def fixture_text(source: str) -> str:
    name, encoding = FIXTURES[source]
    return _decode((FIXTURE_DIR / name).read_bytes(), encoding)


def build_panel(texts: Dict[str, Optional[str]], demo: bool = False) -> PanelData:
    """由四个源的原始文本组装面板；某个源为 None / 解析为空即视为该源失败。"""
    parsed: Dict[str, Dict[str, Quote]] = {}
    for source, _url, _encoding, _headers, parser in SOURCES:
        text = texts.get(source)
        try:
            parsed[source] = parser(text) if text else {}
        except Exception:  # noqa: BLE001 - 接口返回了意料之外的结构：按该源失败处理
            parsed[source] = {}
    rows = merge_panel(parsed[SOURCE_TENCENT], parsed[SOURCE_SINA],
                       parsed[SOURCE_NAVER_KR], parsed[SOURCE_NAVER_JP])
    sources = {source: bool(parsed[source]) for source in parsed}
    return PanelData(rows=rows, sources=sources, demo=demo)


def demo_panel() -> PanelData:
    """离线演示：直接解析 fixtures/ 里留档的实测响应（2026-11-13 收盘）。"""
    return build_panel({source: fixture_text(source) for source in FIXTURES}, demo=True)


def collect_panel(fetch: Callable[..., str] = fetch_text) -> PanelData:
    """并发抓取四个源并组装面板；单个源失败只影响它负责的列，不抛异常。"""
    def load(source_entry) -> Optional[str]:
        _source, url, encoding, headers, _parser = source_entry
        try:
            return fetch(url, encoding=encoding, headers=headers)
        except Exception:  # noqa: BLE001
            return None

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        texts = list(pool.map(load, SOURCES))
    return build_panel({entry[0]: text for entry, text in zip(SOURCES, texts)})
