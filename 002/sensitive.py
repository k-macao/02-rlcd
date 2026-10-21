#!/usr/bin/env python3
"""推送前敏感词检测 —— 对齐国家互联网信息规定，避免违规内容发出。

依据（公开法规，不编造政治人物名单）
--------------------------------
- 《中华人民共和国网络安全法》
- 《互联网信息服务管理办法》
- 《网络信息内容生态治理规定》（国家互联网信息办公室令第 5 号）第六条、第七条

本模块只覆盖法规列明的违法 / 不良信息类别：危害国家安全、分裂国家、恐怖主义与极端主义、
民族仇恨、邪教、淫秽色情、赌博、毒品、违法枪爆、诈骗传销、暴力教唆、侵害未成年人。
**不**把正常财经 / 地缘 / 监管新闻当违规：战争、制裁、Iran、立案、骗局、Ponzi、
Terrorism Risk Insurance、Drug Transit 这类报道用语都不在词库里。

设计（与大盘新鲜度闸门同一套「零依赖 + 环境变量」风格）
------------------------------------------------
1. 归一化：全角→半角、大小写、去零宽字符；中文词忽略夹杂的空格 / 符号（「网 络 赌 场」仍命中）。
2. 英文词按单词边界匹配，避免 crisis 误伤 isis、classification 误伤 class。
3. 合规语境放行：命中词附近出现「打击 / 查处 / 反对 / charges」等执法、驳斥口径时视为新闻报道，不拦截。
4. 推送路径两层闸门：
   - :func:`filter_brief` 先按条剔除违规快讯，再生成 HTML（分析栏目不会引用被删标题）；
   - :func:`check_push` 再扫标题 + HTML，残留命中则取消整次推送。
5. 环境变量：
   - ``SKIP_SENSITIVE_CHECK=1`` 跳过（测试 / 应急）
   - ``SENSITIVE_FORCE=pass|block`` 强制结果（联调）
   - ``SENSITIVE_LEXICON=/path/to.txt`` 外掛词库（一行一词，``block:词`` 为高严重度）

对外接口
--------
    scan(text)          扫描一段文本，返回命中列表（已按词去重）
    filter_brief(brief) 剔除违规快讯，返回 (clean_brief, report)
    check_push(title, content)  推送闸门：ok / reason / hits
    inspect_brief(brief)        只诊断、不修改，供 GET /api/sensitive
    skip_enabled() / reset()
"""
from __future__ import annotations

import html as html_lib
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------- 类别（与《网络信息内容生态治理规定》第六条对齐）
CATEGORIES = {
    "national_security": "危害国家安全",
    "secession": "分裂国家",
    "terrorism": "恐怖主义 / 极端主义",
    "hate": "民族仇恨 / 歧视",
    "cult": "邪教",
    "pornography": "淫秽色情",
    "gambling": "赌博",
    "drugs": "毒品",
    "weapons": "违法枪爆",
    "fraud": "诈骗 / 非法经营",
    "violence": "暴力教唆",
    "minors": "侵害未成年人",
    "custom": "自定义词库",
}

# 高严重度：即便出现在「风险 / 监管」弱语境里也不放行；只有「打击 / 查处 / 反对」等强执法口径才视为报道。
_HIGH = frozenset({"national_security", "secession", "terrorism", "minors", "cult", "weapons"})

# ---------------------------------------------------------------- 词库
# 每条：(词, 类别)。短词、财经常用词（战争 / 制裁 / 骗局 / 博彩 / 彩票 / 独立）一律不收录，
# 避免误伤演示数据与正常快讯。中英、简繁分开写，不依赖第三方分词。
_LEXICON: list[tuple[str, str]] = [
    # —— 危害国家安全（法规原文级短语，不做人物名匹配）——
    ("颠覆国家政权", "national_security"),
    ("推翻社会主义制度", "national_security"),
    ("煽动颠覆", "national_security"),
    ("分裂国家", "national_security"),
    ("破坏国家统一", "national_security"),
    ("泄露国家秘密", "national_security"),
    ("危害国家安全", "national_security"),
    # —— 分裂国家（官方新闻「反对台独」由合规语境放行）——
    ("台独", "secession"),
    ("台獨", "secession"),
    ("台湾独立", "secession"),
    ("台灣獨立", "secession"),
    ("藏独", "secession"),
    ("藏獨", "secession"),
    ("疆独", "secession"),
    ("疆獨", "secession"),
    ("港独", "secession"),
    ("港獨", "secession"),
    ("东突", "secession"),
    ("東突", "secession"),
    # —— 恐怖主义 / 极端主义（不用 terrorism 单词，以免误伤 Terrorism Risk Insurance）——
    ("宣扬恐怖主义", "terrorism"),
    ("宣揚恐怖主義", "terrorism"),
    ("恐怖组织", "terrorism"),
    ("恐怖組織", "terrorism"),
    ("极端主义", "terrorism"),
    ("極端主義", "terrorism"),
    ("圣战组织", "terrorism"),
    ("isis", "terrorism"),
    ("islamic state", "terrorism"),
    # —— 民族仇恨 ——
    ("民族仇恨", "hate"),
    ("种族灭绝", "hate"),
    ("種族滅絕", "hate"),
    # —— 邪教（官方认定名称；「揭批 / 打击」语境放行）——
    ("邪教组织", "cult"),
    ("邪教組織", "cult"),
    ("法轮功", "cult"),
    ("法輪功", "cult"),
    # —— 淫秽色情 ——
    ("色情网站", "pornography"),
    ("色情網站", "pornography"),
    ("色情视频", "pornography"),
    ("色情視頻", "pornography"),
    ("色情直播", "pornography"),
    ("淫秽视频", "pornography"),
    ("淫穢視頻", "pornography"),
    ("淫秽色情", "pornography"),
    ("成人影片", "pornography"),
    ("成人色情", "pornography"),
    ("黄色网站", "pornography"),
    ("黃色網站", "pornography"),
    ("黄色视频", "pornography"),
    ("情色网站", "pornography"),
    ("裸聊", "pornography"),
    ("约炮", "pornography"),
    ("約炮", "pornography"),
    ("援交", "pornography"),
    ("卖淫", "pornography"),
    ("賣淫", "pornography"),
    ("嫖娼", "pornography"),
    ("av资源", "pornography"),
    ("av資源", "pornography"),
    ("pornhub", "pornography"),
    ("child porn", "minors"),
    ("child pornography", "minors"),
    ("儿童色情", "minors"),
    ("兒童色情", "minors"),
    # —— 赌博（不用「博彩 / 六合彩 / 赌场」光杆词：澳博、港彩、博彩股是合法财经新闻）——
    ("网络赌场", "gambling"),
    ("網路賭場", "gambling"),
    ("网络赌博", "gambling"),
    ("網路賭博", "gambling"),
    ("线上赌场", "gambling"),
    ("線上賭場", "gambling"),
    ("线上赌博", "gambling"),
    ("赌博网站", "gambling"),
    ("賭博網站", "gambling"),
    ("赌球网站", "gambling"),
    ("賭球網站", "gambling"),
    ("私彩", "gambling"),
    ("地下六合彩", "gambling"),
    ("时时彩", "gambling"),
    ("開戶送彩金", "gambling"),
    ("开户送彩金", "gambling"),
    ("博彩平台", "gambling"),
    ("在线博彩", "gambling"),
    ("線上博彩", "gambling"),
    # —— 毒品（不用 drug 单词，以免误伤 Major Drug Transit 总统文件）——
    ("冰毒", "drugs"),
    ("海洛因", "drugs"),
    ("摇头丸", "drugs"),
    ("搖頭丸", "drugs"),
    ("制毒方法", "drugs"),
    ("制毒教程", "drugs"),
    ("贩毒团伙", "drugs"),
    ("販毒團伙", "drugs"),
    ("购毒渠道", "drugs"),
    # —— 违法枪爆 ——
    ("买卖枪支", "weapons"),
    ("買賣槍支", "weapons"),
    ("枪支弹药", "weapons"),
    ("槍支彈藥", "weapons"),
    ("制枪教程", "weapons"),
    ("炸药配方", "weapons"),
    ("炸藥配方", "weapons"),
    ("炸弹制作", "weapons"),
    ("炸彈製作", "weapons"),
    # —— 诈骗 / 非法经营（不用 骗局 / ponzi：知乎「离谱的骗局」、SEC Ponzi Scheme 是新闻）——
    ("代开发票", "fraud"),
    ("代開發票", "fraud"),
    ("代办假证", "fraud"),
    ("代辦假證", "fraud"),
    ("假文凭", "fraud"),
    ("洗钱服务", "fraud"),
    ("洗錢服務", "fraud"),
    ("资金盘平台", "fraud"),
    ("殺豬盤", "fraud"),
    ("杀猪盘", "fraud"),
    ("刷单返利", "fraud"),
    ("刷單返利", "fraud"),
    ("传销组织", "fraud"),
    ("傳銷組織", "fraud"),
    ("发展下线", "fraud"),
    ("發展下線", "fraud"),
    ("传销活动", "fraud"),
    # —— 暴力教唆 ——
    ("杀人教程", "violence"),
    ("殺人教程", "violence"),
    ("虐杀视频", "violence"),
    ("虐殺視頻", "violence"),
    ("斩首视频", "violence"),
    ("斬首視頻", "violence"),
    ("自杀教程", "violence"),
    ("自殺教程", "violence"),
    ("自杀方法", "violence"),
    ("自殺方法", "violence"),
]

# 强合规语境：执法、驳斥、官方反对。高 / 中严重度都可以靠这些放行。
_ALLOW_STRONG = (
    "打击", "打擊", "查处", "查處", "严禁", "嚴禁", "反对", "反對", "谴责", "譴責",
    "防范", "防範", "禁止", "扫除", "掃除", "整治", "依法", "违法", "違法", "犯罪",
    "破获", "破獲", "严惩", "嚴懲", "判刑", "逮捕", "立案", "起诉", "起訴", "通缉", "通緝",
    "扫黄", "掃黃", "打非", "禁毒", "反恐", "揭批", "驳斥", "駁斥", "粉碎", "警惕", "谨防", "謹防",
    "cracked down", "crackdown", "charges", "charged", "prosecut", "arrest",
    "enforcement", "banned", "illegal", "combat", "against", "anti-",
    "warns", "warning", "fight", "fighting",
)
# 弱语境：只放行中严重度（赌博 / 色情 / 诈骗报道里常出现「风险 / 监管」）。
_ALLOW_WEAK = ("风险", "風險", "监管", "監管", "警示", "警告", "案件", "嫌犯")

_ZW = frozenset("\u200b\u200c\u200d\ufeff\u00ad\u2060")
_SEP = frozenset(
    " \t\r\n\u3000.*·•●○◦_~`、，,。.!！?？;；:：|｜/\\-—–−+@#$%^&=\'\"“”‘’"
    "「」『』【】[]()（）<>《》…☆★■□▲△◆◇、"
)
_TAG_RE = re.compile(r"<[^>]+>")

_ENTRIES: list[dict] | None = None

# ---------------------------------------------------------------- 环境变量 / 时间


def skip_enabled() -> bool:
    """SKIP_SENSITIVE_CHECK=1/true/yes/on 时跳过检测（测试 / 应急）。"""
    return os.environ.get("SKIP_SENSITIVE_CHECK", "").strip().lower() in ("1", "true", "yes", "on")


def _force() -> str:
    return os.environ.get("SENSITIVE_FORCE", "").strip().lower()


def _now_cn() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def reset() -> None:
    """测试用：清掉词库缓存，使 ``SENSITIVE_LEXICON`` 重新生效。"""
    global _ENTRIES
    _ENTRIES = None


# ---------------------------------------------------------------- 归一化


def strip_html(text: str) -> str:
    """去掉 HTML 标签再反转义，避免 ``<span>赌</span>场`` 漏检、也避免 CSS 误伤。"""
    return html_lib.unescape(_TAG_RE.sub(" ", text or ""))


def _fold_char(ch: str) -> str:
    if ch in _ZW:
        return ""
    code = ord(ch)
    if 0xFF01 <= code <= 0xFF5E:  # 全角 ASCII
        ch = chr(code - 0xFEE0)
    return ch.lower() if ch.isascii() else ch


def _mapped(text: str, drop_sep: bool) -> tuple[str, list[int]]:
    """返回 (归一化文本, 每个归一化字符对应的原文下标)。

    ``drop_sep=False``：只去零宽、折全角 / 大小写，留给英文单词边界。
    ``drop_sep=True``：再丢掉空白与符号，留给中文「网 络 赌 场」变体。
    """
    chars, mapping = [], []
    for index, ch in enumerate(text or ""):
        folded = _fold_char(ch)
        if not folded:
            continue
        if drop_sep and folded in _SEP:
            continue
        chars.append(folded)
        mapping.append(index)
    return "".join(chars), mapping


def _fold(text: str) -> str:
    """全角→半角、ASCII 小写、去掉零宽字符；保留空白与标点供英文边界匹配。"""
    return _mapped(text, drop_sep=False)[0]


def _compact(text: str) -> str:
    """中文匹配用：在 fold 后再丢掉分隔符，使「网 络 赌 场」「网*络*赌*场」仍命中。"""
    return _mapped(text, drop_sep=True)[0]


def _ascii_regex(word: str) -> re.Pattern:
    parts = [p for p in re.split(r"[\s\W_]+", word.strip().lower()) if p]
    body = r"[\s\W_]*".join(re.escape(p) for p in parts) or re.escape(word.lower())
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")


def _is_ascii_term(word: str) -> bool:
    return all(ord(ch) < 128 for ch in word)


# ---------------------------------------------------------------- 词库装配


def _entry(word: str, category: str) -> dict | None:
    word = (word or "").strip()
    if not word:
        return None
    category = category if category in CATEGORIES else "custom"
    key = _compact(word)
    if len(key) < 2:
        return None
    ascii_term = _is_ascii_term(word)
    return {
        "word": word,
        "key": key,
        "category": category,
        "label": CATEGORIES[category],
        "severity": "high" if category in _HIGH else "medium",
        "ascii": ascii_term,
        "regex": _ascii_regex(word) if ascii_term else None,
    }


def _load_extra_file(path: str) -> list[dict]:
    extra = []
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return extra
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        category = "custom"
        if ":" in line:
            prefix, rest = line.split(":", 1)
            prefix = prefix.strip().lower()
            if prefix in CATEGORIES:
                category, line = prefix, rest.strip()
            elif prefix in ("block", "high"):
                category, line = "national_security", rest.strip()
            elif prefix in ("drop", "medium"):
                line = rest.strip()
        item = _entry(line, category)
        if item:
            extra.append(item)
    return extra


def _entries() -> list[dict]:
    global _ENTRIES
    if _ENTRIES is None:
        built = []
        seen = set()
        for word, category in _LEXICON:
            item = _entry(word, category)
            if item and item["key"] not in seen:
                seen.add(item["key"])
                built.append(item)
        extra_path = (os.environ.get("SENSITIVE_LEXICON") or "").strip()
        if extra_path:
            for item in _load_extra_file(extra_path):
                if item["key"] not in seen:
                    seen.add(item["key"])
                    built.append(item)
        _ENTRIES = built
    return _ENTRIES


# ---------------------------------------------------------------- 扫描


def _allowed_context(text: str, start: int, end: int, severity: str) -> bool:
    """命中词附近若是执法 / 驳斥口径，视为新闻报道，不拦截。"""
    window = text[max(0, start - 32): min(len(text), end + 32)]
    lowered = window.lower()
    tokens = _ALLOW_STRONG if severity == "high" else _ALLOW_STRONG + _ALLOW_WEAK
    for token in tokens:
        if not token:
            continue
        if token.isascii():
            if token.lower() in lowered:
                return True
        elif token in window:
            return True
    return False


def _span_allowed(plain: str, mapping: list[int], start: int, length: int, severity: str) -> bool:
    """把归一化下标映回原文，再看窗口里是不是执法 / 驳斥口径。"""
    if start < 0 or length <= 0 or start + length > len(mapping):
        return False
    orig_s = mapping[start]
    orig_e = mapping[start + length - 1] + 1
    return _allowed_context(plain, orig_s, orig_e, severity)


def scan(text: str) -> list[dict]:
    """扫描一段文本。返回 ``[{word, category, label, severity}]``，按词去重、保持词库顺序。

    先剥 HTML 再匹配，所以推送正文里被 ``<span>`` 切开的词也能命中。
    """
    plain = strip_html(text)
    if not plain:
        return []
    folded, fold_map = _mapped(plain, drop_sep=False)
    compact, compact_map = _mapped(plain, drop_sep=True)
    hits, seen = [], set()
    for ent in _entries():
        found = False
        if ent["ascii"]:
            for match in ent["regex"].finditer(folded):
                if _span_allowed(plain, fold_map, match.start(), match.end() - match.start(),
                                 ent["severity"]):
                    continue
                found = True
                break
        else:
            key = ent["key"]
            start = 0
            while True:
                index = compact.find(key, start)
                if index < 0:
                    break
                if _span_allowed(plain, compact_map, index, len(key), ent["severity"]):
                    start = index + 1
                    continue
                found = True
                break
        if found and ent["word"] not in seen:
            seen.add(ent["word"])
            hits.append({
                "word": ent["word"],
                "category": ent["category"],
                "label": ent["label"],
                "severity": ent["severity"],
            })
    return hits


def _reason_from_hits(hits: list[dict], verb: str) -> str:
    labels = list(dict.fromkeys(h["label"] for h in hits))
    cats = "、".join(labels) if labels else "未分类"
    return f"检出 {len(labels)} 类敏感词（{cats}），已{verb}"


# ---------------------------------------------------------------- 闸门


def filter_brief(brief: dict | None) -> tuple[dict, dict]:
    """按条剔除违规快讯。返回 ``(clean_brief, report)``。

    合规语境（打击 / 查处 / 反对 …）下的报道会保留。源的 key 始终保留（可能变成空列表），
    以免打乱 ``build_html`` 的板块分组。
    """
    dropped = []
    clean = {}
    for name, items in (brief or {}).items():
        kept = []
        for item in items or []:
            title = str((item or {}).get("title") or "")
            hits = scan(title) if title else []
            if hits:
                dropped.append({
                    "source": name,
                    "title": title,
                    "hits": hits,
                    "categories": [h["label"] for h in hits],
                })
            else:
                kept.append(item)
        clean[name] = kept
    labels = list(dict.fromkeys(cat for row in dropped for cat in row["categories"]))
    report = {
        "ok": not dropped,
        "dropped_count": len(dropped),
        "dropped": dropped,
        "categories": labels,
        "reason": ("未检出违规快讯" if not dropped
                   else _reason_from_hits(
                       [h for row in dropped for h in row["hits"]], "剔除相应快讯")),
        "checked_at": _now_cn(),
        "lexicon_size": len(_entries()),
    }
    return clean, report


def inspect_brief(brief: dict | None) -> dict:
    """只诊断、不修改。供 ``GET /api/sensitive`` 与排障。"""
    _, report = filter_brief(brief)
    report["ok"] = report["dropped_count"] == 0
    return report


def check_push(title: str, content: str) -> dict:
    """推送闸门：扫描标题 + HTML 正文。返回 ok / reason / hits（结构对齐 freshness）。

    ``SKIP_SENSITIVE_CHECK``、``SENSITIVE_FORCE=pass|block`` 在这里统一生效，调用方不必再判断。
    """
    info = {
        "ok": True,
        "reason": "",
        "hits": [],
        "categories": [],
        "checked_at": _now_cn(),
        "lexicon_size": len(_entries()),
    }
    if skip_enabled():
        info["skipped"] = True
        info["reason"] = "SKIP_SENSITIVE_CHECK 已启用，跳过敏感词检测（仅限测试/应急）"
        return info

    force = _force()
    if force in ("pass", "block"):
        info["forced"] = True
        info["ok"] = force == "pass"
        info["reason"] = (f"SENSITIVE_FORCE={force} 强制结果（测试/应急）")
        return info

    hits = scan(title or "") + scan(content or "")
    # 标题与正文可能命中同一词，再去一次重。
    uniq, seen = [], set()
    for hit in hits:
        if hit["word"] in seen:
            continue
        seen.add(hit["word"])
        uniq.append(hit)
    info["hits"] = uniq
    info["categories"] = list(dict.fromkeys(h["label"] for h in uniq))
    if uniq:
        info["ok"] = False
        info["reason"] = _reason_from_hits(uniq, "取消推送")
    else:
        info["reason"] = "未检出违规内容，推送放行"
    return info


if __name__ == "__main__":
    import json
    import sys
    blob = sys.stdin.read() if not sys.argv[1:] else " ".join(sys.argv[1:])
    found = scan(blob)
    print(json.dumps({"ok": not found, "hits": found}, ensure_ascii=False, indent=2))
