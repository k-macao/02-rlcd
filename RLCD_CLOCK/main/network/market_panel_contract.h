// 定义全球行情数据面板的行目录、行情接口解析、合并规则、数字格式与刷新节奏。
//
// 面板固定 12 行，市场顺序 A、港、美、韩、日；每行只有最新点位、涨跌幅、
// 成交量（股）和成交额（本币）。设备每分钟直连四个公开行情接口：
//   腾讯证券 qt.gtimg.cn   A/港/美指数全量字段；日韩指数只有点位与涨跌幅
//   新浪财经 hq.sinajs.cn  港股指数成交股数（腾讯港股指数只给成交额）
//   Naver 金融 polling     KOSPI/KOSDAQ 实时行情；日经 225/TOPIX 成交量（延迟 15 分钟）
//
// 本文件只有纯逻辑，不依赖 ESP-IDF，主机测试直接用 device_brief/fixtures/ 里
// 留档的实测响应回归。服务器渲染器 device_brief/market_quotes.py 实现了同一套
// 解析、合并与格式规则，两边任何一处改动都必须同步另一处。
#pragma once

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace market_panel {

inline constexpr int kRowCount = 12;
inline constexpr int kMarketCount = 5;

// 每分钟刷新一次；连续失败后退到 5 分钟，避免断网时每分钟空转一次 Wi-Fi。
inline constexpr uint32_t kRefreshSeconds = 60;
inline constexpr uint32_t kBackoffRefreshSeconds = 5 * 60;
inline constexpr uint8_t kBackoffFailureThreshold = 3;
// 实时面板超过 10 分钟没有刷新成功，且服务器位图读取得更晚时，改显示位图兜底。
inline constexpr uint32_t kLiveStaleSeconds = 10 * 60;
// 页脚「实时」标记的有效期：超过 3 个刷新周期没更新就改写「上次」。
inline constexpr uint32_t kLiveFreshSeconds = 3 * kRefreshSeconds;
// 单个数据源临时失败时，它负责的行沿用上一次数值的最长时间。
inline constexpr uint32_t kRowKeepSeconds = 10 * 60;
// 一轮刷新至少要拿到这么多行有效行情才算成功（与服务器渲染器的 --min-rows 相同）；
// 不足时保留上一版面板并记一次失败。
inline constexpr int kMinLiveRows = 9;
// 辅助数据源（新浪、Naver）连续失败这么多轮后，每隔 kSourceSkipCycles 轮才再试一次：
// 所在网络访问不了某个源时，不必每分钟都为它等一次超时。
inline constexpr uint8_t kSourceSkipFailureThreshold = 3;
inline constexpr uint8_t kSourceSkipCycles = 10;
// 指数单日涨跌幅超过这个值按脏数据丢弃。
inline constexpr double kMaxAbsPct = 50.0;
// 日经 225 盘中腾讯实时点位与 Naver 延迟点位的最大可信偏差。
inline constexpr double kN225SourceTolerance = 0.03;

static_assert(kRefreshSeconds == 60, "market panel refreshes once per minute");
static_assert(kBackoffRefreshSeconds > kRefreshSeconds,
              "failure backoff must be slower than the regular refresh");
static_assert(kBackoffFailureThreshold > 0, "backoff needs at least one failure");
static_assert(kLiveStaleSeconds > kLiveFreshSeconds,
              "live view must be marked stale before it is replaced");
static_assert(kMinLiveRows > 0 && kMinLiveRows <= kRowCount,
              "live row threshold must be reachable");
static_assert(kSourceSkipFailureThreshold > 0 && kSourceSkipCycles > 0,
              "source skip policy needs positive thresholds");

enum class Market : uint8_t {
    kAShare,
    kHongKong,
    kUnitedStates,
    kKorea,
    kJapan,
};

struct RowSpec {
    const char *key;
    Market market;
    const char *name;
};

// 行下标同时是各解析函数输出数组的下标。
inline constexpr int kRowSh000001 = 0;
inline constexpr int kRowSz399001 = 1;
inline constexpr int kRowSz399006 = 2;
inline constexpr int kRowHkHsi = 3;
inline constexpr int kRowHkHstech = 4;
inline constexpr int kRowUsDji = 5;
inline constexpr int kRowUsIxic = 6;
inline constexpr int kRowUsInx = 7;
inline constexpr int kRowKospi = 8;
inline constexpr int kRowKosdaq = 9;
inline constexpr int kRowN225 = 10;
inline constexpr int kRowTopx = 11;

// 行顺序即显示顺序；必须与 device_brief/market_quotes.py 的 PANEL_ROWS 逐行一致。
inline constexpr RowSpec kRows[kRowCount] = {
    {"sh000001", Market::kAShare, "上证指数"},
    {"sz399001", Market::kAShare, "深证成指"},
    {"sz399006", Market::kAShare, "创业板指"},
    {"hkHSI", Market::kHongKong, "恒生指数"},
    {"hkHSTECH", Market::kHongKong, "恒生科技"},
    {"usDJI", Market::kUnitedStates, "道琼斯"},
    {"usIXIC", Market::kUnitedStates, "纳斯达克"},
    {"usINX", Market::kUnitedStates, "标普500"},
    {"KOSPI", Market::kKorea, "KOSPI"},
    {"KOSDAQ", Market::kKorea, "KOSDAQ"},
    {"N225", Market::kJapan, "日经225"},
    {"TOPX", Market::kJapan, "东证指数"},
};

inline constexpr const char *kMarketLabels[kMarketCount] = {"A", "港", "美", "韩", "日"};

constexpr const char *market_label(Market market)
{
    return kMarketLabels[static_cast<int>(market)];
}

struct Quote {
    bool valid = false;
    double last = 0.0;
    double pct = 0.0;
    // 成交量（股）与成交额（本币元）；不大于 0 表示该数据源没有这一列。
    double volume = 0.0;
    double turnover = 0.0;
    // Naver 标记已收盘：此时它给的是交易所官方收盘价。
    bool closed = false;
    // 行情时间（UTC 纪元秒）；0 表示数据源没给或无法解析。
    int64_t time = 0;
};

// 一个数据源一轮解析出的行；它不负责的行保持 invalid。
struct SourceQuotes {
    Quote rows[kRowCount];
    bool any = false;
};

// ---------------------------------------------------------------- 文本工具
namespace detail {

inline const char *find_text(const char *begin, const char *end, const char *needle)
{
    if (!begin || !end || !needle) {
        return nullptr;
    }
    const size_t needle_len = strlen(needle);
    if (needle_len == 0 || begin >= end ||
        static_cast<size_t>(end - begin) < needle_len) {
        return nullptr;
    }
    const char *last = end - needle_len;
    for (const char *p = begin; p <= last; ++p) {
        if (*p == needle[0] && memcmp(p, needle, needle_len) == 0) {
            return p;
        }
    }
    return nullptr;
}

inline const char *find_char(const char *begin, const char *end, char target)
{
    if (!begin || !end) {
        return nullptr;
    }
    for (const char *p = begin; p < end; ++p) {
        if (*p == target) {
            return p;
        }
    }
    return nullptr;
}

inline bool span_equals(const char *begin, const char *end, const char *text)
{
    const size_t len = strlen(text);
    return begin && end && static_cast<size_t>(end - begin) == len &&
           memcmp(begin, text, len) == 0;
}

// 解析 [begin, end) 里的十进制数（允许首尾空白、正负号与千分位逗号）。
inline bool parse_number(const char *begin, const char *end, double *out)
{
    if (!begin || !end || !out || begin >= end) {
        return false;
    }
    char buffer[40];
    size_t len = 0;
    for (const char *p = begin; p < end; ++p) {
        if (*p == ',') {
            continue;
        }
        if (len + 1 >= sizeof(buffer)) {
            return false;
        }
        buffer[len++] = *p;
    }
    buffer[len] = '\0';
    char *first = buffer;
    while (*first == ' ' || *first == '\t') {
        ++first;
    }
    if (*first == '\0') {
        return false;
    }
    // 只接受纯十进制写法，挡掉 strtod 也会吃进去的 inf / nan / 十六进制。
    for (const char *p = first; *p; ++p) {
        const bool ok = (*p >= '0' && *p <= '9') || *p == '.' || *p == '-' ||
                        *p == '+' || *p == ' ' || *p == '\t';
        if (!ok) {
            return false;
        }
    }
    char *tail = nullptr;
    const double value = strtod(first, &tail);
    if (tail == first) {
        return false;
    }
    while (*tail == ' ' || *tail == '\t') {
        ++tail;
    }
    if (*tail != '\0' || !isfinite(value)) {
        return false;
    }
    *out = value;
    return true;
}

inline constexpr int kMaxFields = 64;

struct Fields {
    const char *begin[kMaxFields];
    const char *end[kMaxFields];
    int count = 0;

    bool number(int index, double *out) const
    {
        return index >= 0 && index < count &&
               parse_number(begin[index], end[index], out);
    }

    // 该字段是正数时返回其值，否则返回 0（表示「没有这一列」）。
    double positive(int index) const
    {
        double value = 0.0;
        return number(index, &value) && value > 0.0 ? value : 0.0;
    }

    bool has(int index) const
    {
        return index >= 0 && index < count;
    }
};

inline void split_fields(const char *begin, const char *end, char separator, Fields *fields)
{
    fields->count = 0;
    const char *start = begin;
    for (const char *p = begin; p <= end; ++p) {
        if (p == end || *p == separator) {
            if (fields->count < kMaxFields) {
                fields->begin[fields->count] = start;
                fields->end[fields->count] = p;
                ++fields->count;
            }
            start = p + 1;
        }
    }
}

// ---- 行情时间：各数据源给的是交易所当地时间，统一换算成 UTC 纪元秒再比较。
struct CivilTime {
    int year = 0;
    int month = 0;
    int day = 0;
    int hour = 0;
    int minute = 0;
    int second = 0;
};

inline constexpr int kChinaUtcOffsetSeconds = 8 * 3600;

// 公历日期 → 自 1970-01-01 起的天数（Howard Hinnant 的 days_from_civil）。
constexpr int64_t days_from_civil(int year, int month, int day)
{
    const int y = month <= 2 ? year - 1 : year;
    const int64_t era = (y >= 0 ? y : y - 399) / 400;
    const int64_t yoe = y - era * 400;
    const int64_t doy = (153 * (month > 2 ? month - 3 : month + 9) + 2) / 5 + day - 1;
    const int64_t doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + doe - 719468;
}

inline void civil_from_days(int64_t days, int *year, int *month, int *day)
{
    const int64_t z = days + 719468;
    const int64_t era = (z >= 0 ? z : z - 146096) / 146097;
    const int64_t doe = z - era * 146097;
    const int64_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    const int64_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const int64_t mp = (5 * doy + 2) / 153;
    *day = static_cast<int>(doy - (153 * mp + 2) / 5 + 1);
    *month = static_cast<int>(mp < 10 ? mp + 3 : mp - 9);
    *year = static_cast<int>(yoe + era * 400 + (*month <= 2 ? 1 : 0));
}

constexpr bool is_leap_year(int year)
{
    return (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
}

constexpr int days_in_month(int year, int month)
{
    return month == 2 ? (is_leap_year(year) ? 29 : 28)
                      : (month == 4 || month == 6 || month == 9 || month == 11) ? 30 : 31;
}

// 0 = 星期日。
constexpr int weekday_from_days(int64_t days)
{
    return static_cast<int>(((days + 4) % 7 + 7) % 7);
}

constexpr int nth_sunday(int year, int month, int nth)
{
    return 1 + (7 - weekday_from_days(days_from_civil(year, month, 1))) % 7 + 7 * (nth - 1);
}

constexpr bool civil_time_valid(const CivilTime &t)
{
    return t.year >= 1970 && t.year <= 9999 && t.month >= 1 && t.month <= 12 &&
           t.day >= 1 && t.day <= days_in_month(t.year, t.month) &&
           t.hour >= 0 && t.hour <= 23 && t.minute >= 0 && t.minute <= 59 &&
           t.second >= 0 && t.second <= 59;
}

constexpr int64_t civil_to_epoch(const CivilTime &t, int utc_offset_seconds)
{
    return days_from_civil(t.year, t.month, t.day) * 86400 + t.hour * 3600 +
           t.minute * 60 + t.second - utc_offset_seconds;
}

// 美国东部时间：3 月第二个周日 03:00 起到 11 月第一个周日 02:00 前为夏令时（UTC-4），
// 其余为标准时间（UTC-5）。切换当晚不存在 / 重复的那一小时按 Python zoneinfo
// fold=0 的口径处理（美股交易时段不会落在那里）。
constexpr int us_eastern_utc_offset_seconds(const CivilTime &t)
{
    bool dst = t.month > 3 && t.month < 11;
    if (t.month == 3) {
        const int start = nth_sunday(t.year, 3, 2);
        dst = t.day > start || (t.day == start && t.hour >= 3);
    } else if (t.month == 11) {
        const int stop = nth_sunday(t.year, 11, 1);
        dst = t.day < stop || (t.day == stop && t.hour < 2);
    }
    return (dst ? -4 : -5) * 3600;
}

struct Scanner {
    const char *p;
    const char *end;

    bool digits(int min_count, int max_count, int *out)
    {
        int value = 0;
        int count = 0;
        while (p < end && *p >= '0' && *p <= '9' && count < max_count) {
            value = value * 10 + (*p - '0');
            ++p;
            ++count;
        }
        if (count < min_count) {
            return false;
        }
        *out = value;
        return true;
    }

    bool literal(char expected)
    {
        if (p < end && *p == expected) {
            ++p;
            return true;
        }
        return false;
    }

    int spaces()
    {
        int count = 0;
        while (p < end && (*p == ' ' || *p == '\t')) {
            ++p;
            ++count;
        }
        return count;
    }

    bool done() const
    {
        return p >= end;
    }
};

inline void trim_span(const char **begin, const char **end)
{
    while (*begin < *end && (**begin == ' ' || **begin == '\t')) {
        ++*begin;
    }
    while (*end > *begin && ((*end)[-1] == ' ' || (*end)[-1] == '\t' ||
                             (*end)[-1] == '\r' || (*end)[-1] == '\n')) {
        --*end;
    }
}

// "YYYY<sep>M<sep>D"，例如 2026/11/13、2026-11-13。
inline bool scan_date(Scanner *scanner, char separator, CivilTime *out)
{
    return scanner->digits(4, 4, &out->year) && scanner->literal(separator) &&
           scanner->digits(1, 2, &out->month) && scanner->literal(separator) &&
           scanner->digits(1, 2, &out->day);
}

// "H:M:S"。
inline bool scan_clock(Scanner *scanner, CivilTime *out)
{
    return scanner->digits(1, 2, &out->hour) && scanner->literal(':') &&
           scanner->digits(1, 2, &out->minute) && scanner->literal(':') &&
           scanner->digits(1, 2, &out->second);
}

// 紧凑写法 "YYYYMMDDHHMMSS"（腾讯沪深行情）。
inline bool parse_compact_datetime(const char *begin, const char *end, CivilTime *out)
{
    if (!begin || !end || !out) {
        return false;
    }
    trim_span(&begin, &end);
    Scanner scanner{begin, end};
    CivilTime t;
    if (end - begin != 14 || !scanner.digits(4, 4, &t.year) ||
        !scanner.digits(2, 2, &t.month) || !scanner.digits(2, 2, &t.day) ||
        !scanner.digits(2, 2, &t.hour) || !scanner.digits(2, 2, &t.minute) ||
        !scanner.digits(2, 2, &t.second) || !scanner.done() || !civil_time_valid(t)) {
        return false;
    }
    *out = t;
    return true;
}

// 分隔写法 "YYYY<sep>M<sep>D H:M:S"（腾讯港股用 /，美股与环球指数用 -）。
inline bool parse_separated_datetime(const char *begin, const char *end,
                                     char date_separator, CivilTime *out)
{
    if (!begin || !end || !out) {
        return false;
    }
    trim_span(&begin, &end);
    Scanner scanner{begin, end};
    CivilTime t;
    if (!scan_date(&scanner, date_separator, &t) || scanner.spaces() < 1 ||
        !scan_clock(&scanner, &t) || !scanner.done() || !civil_time_valid(t)) {
        return false;
    }
    *out = t;
    return true;
}

// 新浪把日期和时间放在两个字段里："YYYY/M/D" 与 "H:M:S"。
inline bool parse_split_datetime(const char *date_begin, const char *date_end,
                                 const char *clock_begin, const char *clock_end,
                                 CivilTime *out)
{
    if (!date_begin || !date_end || !clock_begin || !clock_end || !out) {
        return false;
    }
    trim_span(&date_begin, &date_end);
    trim_span(&clock_begin, &clock_end);
    Scanner date{date_begin, date_end};
    Scanner clock{clock_begin, clock_end};
    CivilTime t;
    if (!scan_date(&date, '/', &t) || !date.done() || !scan_clock(&clock, &t) ||
        !clock.done() || !civil_time_valid(t)) {
        return false;
    }
    *out = t;
    return true;
}

// ISO 8601 带时区写法（Naver localTradedAt）：
// "YYYY-MM-DDTHH:MM:SS[.fff](Z|±HH[:MM])"；不带时区的写法无法定位到绝对时刻，按未知处理。
inline bool parse_iso_datetime(const char *begin, const char *end, int64_t *out_epoch)
{
    if (!begin || !end || !out_epoch) {
        return false;
    }
    trim_span(&begin, &end);
    Scanner scanner{begin, end};
    CivilTime t;
    if (!scanner.digits(4, 4, &t.year) || !scanner.literal('-') ||
        !scanner.digits(2, 2, &t.month) || !scanner.literal('-') ||
        !scanner.digits(2, 2, &t.day) ||
        !(scanner.literal('T') || scanner.literal(' ')) ||
        !scanner.digits(2, 2, &t.hour) || !scanner.literal(':') ||
        !scanner.digits(2, 2, &t.minute) || !scanner.literal(':') ||
        !scanner.digits(2, 2, &t.second) || !civil_time_valid(t)) {
        return false;
    }
    if (scanner.literal('.')) {
        int fraction = 0;
        if (!scanner.digits(1, 9, &fraction)) {
            return false;
        }
    }
    int offset_seconds = 0;
    if (scanner.literal('Z')) {
        offset_seconds = 0;
    } else {
        int sign = 0;
        if (scanner.literal('+')) {
            sign = 1;
        } else if (scanner.literal('-')) {
            sign = -1;
        } else {
            return false;
        }
        int hours = 0;
        int minutes = 0;
        if (!scanner.digits(2, 2, &hours)) {
            return false;
        }
        if (!scanner.done()) {
            (void)scanner.literal(':');
            if (!scanner.digits(2, 2, &minutes)) {
                return false;
            }
        }
        if (hours > 23 || minutes > 59) {
            return false;
        }
        offset_seconds = sign * (hours * 3600 + minutes * 60);
    }
    if (!scanner.done()) {
        return false;
    }
    *out_epoch = civil_to_epoch(t, offset_seconds);
    return true;
}

// 腾讯行情时间：沪深 [30] 紧凑写法（北京时间）、港股 [30] 斜杠写法（香港时间）、
// 美股 [30] 横杠写法（美国东部时间）、环球指数 [2] 横杠写法（北京时间）。
inline int64_t tencent_quote_time(const Fields &fields, char kind)
{
    CivilTime t;
    if (kind == 'g') {
        return fields.has(2) &&
                       parse_separated_datetime(fields.begin[2], fields.end[2], '-', &t)
                   ? civil_to_epoch(t, kChinaUtcOffsetSeconds)
                   : 0;
    }
    if (!fields.has(30)) {
        return 0;
    }
    const char *begin = fields.begin[30];
    const char *end = fields.end[30];
    if (kind == 'a') {
        return parse_compact_datetime(begin, end, &t)
                   ? civil_to_epoch(t, kChinaUtcOffsetSeconds)
                   : 0;
    }
    if (kind == 'h') {
        return parse_separated_datetime(begin, end, '/', &t)
                   ? civil_to_epoch(t, kChinaUtcOffsetSeconds)
                   : 0;
    }
    return parse_separated_datetime(begin, end, '-', &t)
               ? civil_to_epoch(t, us_eastern_utc_offset_seconds(t))
               : 0;
}

inline bool set_price_and_pct(Quote *quote, bool have_last, double last,
                              bool have_pct, double pct)
{
    if (!have_last || !have_pct || !(last > 0.0) || fabs(pct) > kMaxAbsPct) {
        return false;
    }
    quote->valid = true;
    quote->last = last;
    quote->pct = pct;
    return true;
}

} // namespace detail

// ---------------------------------------------------------------- 解析：腾讯证券
// 响应是若干条 v_代码="字段~字段~…"; 语句（GBK 编码，名称字段不使用）。
// 沪深 / 港 / 美（至少 38 个字段）：[3] 最新 [30] 时间 [32] 涨跌幅% [36] 成交量 [37] 成交额
//   沪深指数 [36] 单位手、[37] 单位万元；
//   港股指数 [36][37] 都是成交额（万港元），没有成交股数；
//   美股指数 [36] 是成交股数，[37] 是「股数×点位」的无意义值，丢弃。
// 环球指数（gz 前缀，至少 6 个字段）：[2] 北京时间 [3] 最新 [5] 涨跌幅%，没有成交量。
inline bool parse_tencent(const char *text, size_t length, SourceQuotes *out)
{
    if (!out) {
        return false;
    }
    *out = SourceQuotes{};
    if (!text || length == 0) {
        return false;
    }
    struct Symbol {
        const char *name;
        int row;
        char kind; // 'a' 沪深 / 'h' 港 / 'u' 美 / 'g' 环球
    };
    static constexpr Symbol kSymbols[] = {
        {"sh000001", kRowSh000001, 'a'}, {"sz399001", kRowSz399001, 'a'},
        {"sz399006", kRowSz399006, 'a'}, {"hkHSI", kRowHkHsi, 'h'},
        {"hkHSTECH", kRowHkHstech, 'h'}, {"usDJI", kRowUsDji, 'u'},
        {"usIXIC", kRowUsIxic, 'u'},     {"usINX", kRowUsInx, 'u'},
        {"gzKS11", kRowKospi, 'g'},      {"gzKOSDAQ", kRowKosdaq, 'g'},
        {"gzN225", kRowN225, 'g'},
    };
    const char *end = text + length;
    const char *cursor = text;
    while (cursor < end) {
        const char *statement = detail::find_text(cursor, end, "v_");
        if (!statement) {
            break;
        }
        const char *symbol_begin = statement + 2;
        const char *equals = detail::find_char(symbol_begin, end, '=');
        if (!equals || equals + 1 >= end || equals[1] != '"') {
            cursor = symbol_begin;
            continue;
        }
        const char *payload = equals + 2;
        const char *payload_end = detail::find_char(payload, end, '"');
        if (!payload_end) {
            break;
        }
        cursor = payload_end + 1;
        const Symbol *symbol = nullptr;
        for (const Symbol &candidate : kSymbols) {
            if (detail::span_equals(symbol_begin, equals, candidate.name)) {
                symbol = &candidate;
                break;
            }
        }
        if (!symbol) {
            continue;
        }
        detail::Fields fields;
        detail::split_fields(payload, payload_end, '~', &fields);
        const bool global_index = symbol->kind == 'g';
        if (fields.count < (global_index ? 6 : 38)) {
            continue;
        }
        Quote quote;
        double last = 0.0;
        double pct = 0.0;
        const bool have_last = fields.number(3, &last);
        const bool have_pct = fields.number(global_index ? 5 : 32, &pct);
        if (!detail::set_price_and_pct(&quote, have_last, last, have_pct, pct)) {
            continue;
        }
        if (!global_index) {
            const double volume = fields.positive(36);
            const double amount = fields.positive(37);
            if (symbol->kind == 'a') {
                quote.volume = volume * 100.0;   // 手 → 股
                quote.turnover = amount * 1e4;   // 万元 → 元
            } else if (symbol->kind == 'h') {
                quote.turnover = amount * 1e4;   // 万港元 → 港元
            } else {
                quote.volume = volume;
            }
        }
        quote.time = detail::tencent_quote_time(fields, symbol->kind);
        out->rows[symbol->row] = quote;
        out->any = true;
    }
    return out->any;
}

// ---------------------------------------------------------------- 解析：新浪港股
// var hq_str_rt_hkHSI="HSI,恒生指数,…"; 逗号分隔（至少 19 个字段）：
//   [6] 最新 [8] 涨跌幅% [11] 成交额（千港元） [12] 成交量（股） [17] 日期 [18] 时间（香港时间）
inline bool parse_sina_hk(const char *text, size_t length, SourceQuotes *out)
{
    if (!out) {
        return false;
    }
    *out = SourceQuotes{};
    if (!text || length == 0) {
        return false;
    }
    struct Symbol {
        const char *prefix;
        int row;
    };
    static constexpr Symbol kSymbols[] = {
        {"hq_str_rt_hkHSI=\"", kRowHkHsi},
        {"hq_str_rt_hkHSTECH=\"", kRowHkHstech},
    };
    const char *end = text + length;
    for (const Symbol &symbol : kSymbols) {
        const char *statement = detail::find_text(text, end, symbol.prefix);
        if (!statement) {
            continue;
        }
        const char *payload = statement + strlen(symbol.prefix);
        const char *payload_end = detail::find_char(payload, end, '"');
        if (!payload_end) {
            continue;
        }
        detail::Fields fields;
        detail::split_fields(payload, payload_end, ',', &fields);
        if (fields.count < 19) {
            continue;
        }
        Quote quote;
        double last = 0.0;
        double pct = 0.0;
        const bool have_last = fields.number(6, &last);
        const bool have_pct = fields.number(8, &pct);
        if (!detail::set_price_and_pct(&quote, have_last, last, have_pct, pct)) {
            continue;
        }
        quote.turnover = fields.positive(11) * 1e3; // 千港元 → 港元
        quote.volume = fields.positive(12);
        detail::CivilTime traded_at;
        if (detail::parse_split_datetime(fields.begin[17], fields.end[17],
                                         fields.begin[18], fields.end[18], &traded_at)) {
            quote.time = detail::civil_to_epoch(traded_at, detail::kChinaUtcOffsetSeconds);
        }
        out->rows[symbol.row] = quote;
        out->any = true;
    }
    return out->any;
}

// ---------------------------------------------------------------- 解析：Naver 金融
// polling JSON 的 datas 数组里每项一个指数；各项以 "itemCode":"…"（韩国）或
// "reutersCode":"…"（环球）开头，键的先后顺序不固定，只取带 Raw 后缀的纯数字字符串。
// 成交额只对韩国指数可信：日经的成交额字段单位标注有误、TOPIX 为空，一律不用。
inline bool parse_naver(const char *text, size_t length, SourceQuotes *out)
{
    if (!out) {
        return false;
    }
    *out = SourceQuotes{};
    if (!text || length == 0) {
        return false;
    }
    struct Symbol {
        const char *marker;
        int row;
        bool domestic;
    };
    static constexpr Symbol kSymbols[] = {
        {"\"itemCode\":\"KOSPI\"", kRowKospi, true},
        {"\"itemCode\":\"KOSDAQ\"", kRowKosdaq, true},
        {"\"reutersCode\":\".N225\"", kRowN225, false},
        {"\"reutersCode\":\".TOPX\"", kRowTopx, false},
    };
    const char *end = text + length;
    // 取 "键":"数字" 里的数字；键不存在或不是数字时返回 false。
    const auto raw_number = [](const char *begin, const char *item_end,
                               const char *key, double *value) {
        const char *found = detail::find_text(begin, item_end, key);
        if (!found) {
            return false;
        }
        const char *value_begin = found + strlen(key);
        const char *value_end = detail::find_char(value_begin, item_end, '"');
        return value_end && detail::parse_number(value_begin, value_end, value);
    };
    const auto raw_positive = [&raw_number](const char *begin, const char *item_end,
                                            const char *key) {
        double value = 0.0;
        return raw_number(begin, item_end, key, &value) && value > 0.0 ? value : 0.0;
    };
    for (const Symbol &symbol : kSymbols) {
        const char *item = detail::find_text(text, end, symbol.marker);
        if (!item) {
            continue;
        }
        const char *body = item + strlen(symbol.marker);
        const char *next_domestic = detail::find_text(body, end, "\"itemCode\":\"");
        const char *next_world = detail::find_text(body, end, "\"reutersCode\":\"");
        const char *item_end = end;
        if (next_domestic && next_domestic < item_end) {
            item_end = next_domestic;
        }
        if (next_world && next_world < item_end) {
            item_end = next_world;
        }
        Quote quote;
        double last = 0.0;
        double pct = 0.0;
        const bool have_last =
            raw_number(body, item_end, "\"closePriceRaw\":\"", &last);
        const bool have_pct =
            raw_number(body, item_end, "\"fluctuationsRatioRaw\":\"", &pct);
        if (!detail::set_price_and_pct(&quote, have_last, last, have_pct, pct)) {
            continue;
        }
        quote.volume =
            raw_positive(body, item_end, "\"accumulatedTradingVolumeRaw\":\"");
        if (symbol.domestic) {
            quote.turnover =
                raw_positive(body, item_end, "\"accumulatedTradingValueRaw\":\"");
        }
        quote.closed =
            detail::find_text(body, item_end, "\"marketStatus\":\"CLOSE\"") != nullptr;
        static constexpr char kTradedAtKey[] = "\"localTradedAt\":\"";
        const char *traded_at = detail::find_text(body, item_end, kTradedAtKey);
        if (traded_at) {
            const char *value_begin = traded_at + sizeof(kTradedAtKey) - 1;
            const char *value_end = detail::find_char(value_begin, item_end, '"');
            int64_t epoch = 0;
            if (value_end && detail::parse_iso_datetime(value_begin, value_end, &epoch)) {
                quote.time = epoch;
            }
        }
        out->rows[symbol.row] = quote;
        out->any = true;
    }
    return out->any;
}

// ---------------------------------------------------------------- 合并四个数据源
// 规则与 market_quotes.py 的 merge_panel 相同：
//   A / 美：只用腾讯；
//   港：腾讯给点位 / 涨跌幅 / 成交额，新浪补成交股数；腾讯缺行时整行用新浪；
//   韩：Naver 是交易所实时行情且带成交量；Naver 失败时腾讯只能给点位；
//   日经：盘中用腾讯实时点位 + Naver 成交量，收盘后或两者偏差过大时整行用 Naver；
//   TOPIX：腾讯 / 新浪的 TOPIX 是坏数据，只信 Naver。
inline void merge_sources(const SourceQuotes &tencent,
                          const SourceQuotes &sina,
                          const SourceQuotes &naver_kr,
                          const SourceQuotes &naver_jp,
                          Quote out[kRowCount])
{
    for (int row = 0; row < kRowCount; ++row) {
        out[row] = Quote{};
    }
    for (int row = kRowSh000001; row <= kRowSz399006; ++row) {
        out[row] = tencent.rows[row];
    }
    for (int row = kRowUsDji; row <= kRowUsInx; ++row) {
        out[row] = tencent.rows[row];
    }
    for (int row = kRowHkHsi; row <= kRowHkHstech; ++row) {
        const Quote &base = tencent.rows[row];
        const Quote &extra = sina.rows[row];
        if (!base.valid) {
            out[row] = extra;
            continue;
        }
        out[row] = base;
        if (extra.valid) {
            out[row].volume = extra.volume;
            if (!(out[row].turnover > 0.0)) {
                out[row].turnover = extra.turnover;
            }
        }
    }
    for (int row = kRowKospi; row <= kRowKosdaq; ++row) {
        out[row] = naver_kr.rows[row].valid ? naver_kr.rows[row] : tencent.rows[row];
    }
    {
        const Quote &delayed = naver_jp.rows[kRowN225];
        const Quote &realtime = tencent.rows[kRowN225];
        if (!delayed.valid) {
            out[kRowN225] = realtime;
        } else if (!realtime.valid || delayed.closed ||
                   fabs(realtime.last - delayed.last) >
                       delayed.last * kN225SourceTolerance) {
            out[kRowN225] = delayed;
        } else {
            out[kRowN225] = realtime;
            out[kRowN225].volume = delayed.volume;
            out[kRowN225].closed = delayed.closed;
        }
    }
    out[kRowTopx] = naver_jp.rows[kRowTopx];
}

inline int valid_row_count(const Quote rows[kRowCount])
{
    int count = 0;
    for (int row = 0; row < kRowCount; ++row) {
        if (rows[row].valid) {
            ++count;
        }
    }
    return count;
}

// 本轮没取到的行沿用上一轮数值，最多沿用 kRowKeepSeconds；时间用开机秒数，不受对时影响。
inline void carry_over_rows(const Quote previous[kRowCount],
                            const uint32_t previous_updated_at[kRowCount],
                            const Quote fresh[kRowCount],
                            uint32_t now_seconds,
                            Quote out[kRowCount],
                            uint32_t out_updated_at[kRowCount])
{
    for (int row = 0; row < kRowCount; ++row) {
        if (fresh[row].valid) {
            out[row] = fresh[row];
            out_updated_at[row] = now_seconds;
            continue;
        }
        const bool keep = previous[row].valid &&
                          now_seconds >= previous_updated_at[row] &&
                          now_seconds - previous_updated_at[row] < kRowKeepSeconds;
        out[row] = keep ? previous[row] : Quote{};
        out_updated_at[row] = keep ? previous_updated_at[row] : 0;
    }
}

// ---------------------------------------------------------------- 数字格式
inline constexpr const char *kEmptyCell = "--";
inline constexpr size_t kCellTextBytes = 24;
// 超出这些上限的数值不可能是真实行情，直接显示为空，也保证定长缓冲区放得下。
inline constexpr double kMaxPriceValue = 1e9;
inline constexpr double kMaxPctValue = 1e6;
inline constexpr double kMaxAmountValue = 1e18;

namespace detail {

// 有界追加：放不下的部分直接截断，结果始终以 NUL 结尾；返回新的写入位置。
inline size_t append_text(char *out, size_t out_len, size_t pos, const char *text)
{
    if (!out || out_len == 0) {
        return pos;
    }
    if (pos >= out_len) {
        pos = out_len - 1;
    }
    for (; text && *text && pos + 1 < out_len; ++text) {
        out[pos++] = *text;
    }
    out[pos] = '\0';
    return pos;
}

inline void copy_text(char *out, size_t out_len, const char *text)
{
    (void)append_text(out, out_len, 0, text);
}

// 给 "12345.67" 这样的十进制串的整数部分加千分位逗号。
inline void group_thousands(const char *plain, char *out, size_t out_len)
{
    if (!out || out_len == 0) {
        return;
    }
    const char *dot = strchr(plain, '.');
    const size_t int_len = dot ? static_cast<size_t>(dot - plain) : strlen(plain);
    size_t pos = 0;
    for (size_t i = 0; i < int_len && pos + 1 < out_len; ++i) {
        if (i > 0 && (int_len - i) % 3 == 0 && pos + 2 < out_len) {
            out[pos++] = ',';
        }
        out[pos++] = plain[i];
    }
    for (const char *p = plain + int_len; *p && pos + 1 < out_len; ++p) {
        out[pos++] = *p;
    }
    out[pos] = '\0';
}

inline void format_scaled(double scaled, const char *unit, char *out, size_t out_len)
{
    char plain[48];
    if (scaled < 9.995) {
        snprintf(plain, sizeof(plain), "%.2f", scaled);
    } else if (scaled < 99.95) {
        snprintf(plain, sizeof(plain), "%.1f", scaled);
    } else {
        snprintf(plain, sizeof(plain), "%.0f", scaled);
    }
    char grouped[64];
    group_thousands(plain, grouped, sizeof(grouped));
    const size_t pos = append_text(out, out_len, 0, grouped);
    (void)append_text(out, out_len, pos, unit);
}

} // namespace detail

// 点位：千分位 + 两位小数，例如 61,142.35。
inline void format_price(double value, char *out, size_t out_len)
{
    if (!isfinite(value) || !(value > 0.0) || value >= kMaxPriceValue) {
        detail::copy_text(out, out_len, kEmptyCell);
        return;
    }
    char plain[48];
    snprintf(plain, sizeof(plain), "%.2f", value);
    detail::group_thousands(plain, out, out_len);
}

// 涨跌幅：始终带正负号，四舍五入后为零时写 0.00%。
inline void format_pct(double value, char *out, size_t out_len)
{
    if (!isfinite(value) || fabs(value) >= kMaxPctValue) {
        detail::copy_text(out, out_len, kEmptyCell);
        return;
    }
    char body[48];
    snprintf(body, sizeof(body), "%.2f", fabs(value));
    bool zero = true;
    for (const char *p = body; *p; ++p) {
        if (*p != '0' && *p != '.') {
            zero = false;
            break;
        }
    }
    if (zero) {
        detail::copy_text(out, out_len, "0.00%");
        return;
    }
    size_t pos = detail::append_text(out, out_len, 0, value > 0.0 ? "+" : "-");
    pos = detail::append_text(out, out_len, pos, body);
    (void)detail::append_text(out, out_len, pos, "%");
}

// 成交量 / 成交额：按 万 / 亿 / 万亿 取三到四位有效数字，例如 472亿、1.66万亿。
inline void format_cn_number(double value, char *out, size_t out_len)
{
    if (!isfinite(value) || !(value > 0.0) || value >= kMaxAmountValue) {
        detail::copy_text(out, out_len, kEmptyCell);
        return;
    }
    if (value >= 9999.5e8) {
        detail::format_scaled(value / 1e12, "万亿", out, out_len);
    } else if (value >= 9999.5e4) {
        detail::format_scaled(value / 1e8, "亿", out, out_len);
    } else if (value >= 9999.5) {
        detail::format_scaled(value / 1e4, "万", out, out_len);
    } else {
        char plain[48];
        snprintf(plain, sizeof(plain), "%.0f", value);
        detail::group_thousands(plain, out, out_len);
    }
}

struct RowText {
    char price[kCellTextBytes];
    char pct[kCellTextBytes];
    char volume[kCellTextBytes];
    char turnover[kCellTextBytes];
};

// 一行四个单元格的文字；与 render_brief.py 的 row_cells 输出逐字相同。
inline void format_row(const Quote &quote, RowText *text)
{
    if (!text) {
        return;
    }
    if (!quote.valid) {
        detail::copy_text(text->price, sizeof(text->price), kEmptyCell);
        detail::copy_text(text->pct, sizeof(text->pct), kEmptyCell);
        detail::copy_text(text->volume, sizeof(text->volume), kEmptyCell);
        detail::copy_text(text->turnover, sizeof(text->turnover), kEmptyCell);
        return;
    }
    format_price(quote.last, text->price, sizeof(text->price));
    format_pct(quote.pct, text->pct, sizeof(text->pct));
    format_cn_number(quote.volume, text->volume, sizeof(text->volume));
    format_cn_number(quote.turnover, text->turnover, sizeof(text->turnover));
}

// 一行在屏幕上的四格文字是否相同。
inline bool row_text_equal(const Quote &a, const Quote &b)
{
    RowText left;
    RowText right;
    format_row(a, &left);
    format_row(b, &right);
    return strcmp(left.price, right.price) == 0 && strcmp(left.pct, right.pct) == 0 &&
           strcmp(left.volume, right.volume) == 0 && strcmp(left.turnover, right.turnover) == 0;
}

// 「两市成交额」= 沪市 + 深市；任一缺失时返回 0（显示为 --）。
inline double two_market_turnover(const Quote rows[kRowCount])
{
    const Quote &shanghai = rows[kRowSh000001];
    const Quote &shenzhen = rows[kRowSz399001];
    if (!shanghai.valid || !shenzhen.valid || !(shanghai.turnover > 0.0) ||
        !(shenzhen.turnover > 0.0)) {
        return 0.0;
    }
    return shanghai.turnover + shenzhen.turnover;
}

inline constexpr const char *kTwoMarketLabel = "两市成交";

inline void format_two_market(const Quote rows[kRowCount], char *out, size_t out_len)
{
    char amount[kCellTextBytes];
    format_cn_number(two_market_turnover(rows), amount, sizeof(amount));
    size_t pos = detail::append_text(out, out_len, 0, kTwoMarketLabel);
    pos = detail::append_text(out, out_len, pos, " ");
    (void)detail::append_text(out, out_len, pos, amount);
}

// ---------------------------------------------------------------- 行情时间
// 全表最新一笔行情的时间（UTC 纪元秒）；没有任何一行带时间时返回 0。
// 休市期间它不变，面板也就不会无谓重绘。
inline int64_t latest_quote_time(const Quote rows[kRowCount])
{
    int64_t latest = 0;
    for (int row = 0; row < kRowCount; ++row) {
        if (rows[row].valid && rows[row].time > latest) {
            latest = rows[row].time;
        }
    }
    return latest;
}

inline constexpr size_t kAsOfTextBytes = 16;

// 右上角的行情时间：北京时间 "MM-DD HH:MM"；未知时写 "--"。
inline void format_as_of(int64_t epoch_seconds, char *out, size_t out_len)
{
    if (epoch_seconds <= 0) {
        detail::copy_text(out, out_len, kEmptyCell);
        return;
    }
    const int64_t local = epoch_seconds + detail::kChinaUtcOffsetSeconds;
    const int64_t days = local / 86400;
    const int seconds_of_day = static_cast<int>(local % 86400);
    int year = 0;
    int month = 0;
    int day = 0;
    detail::civil_from_days(days, &year, &month, &day);
    const int hour = seconds_of_day / 3600;
    const int minute = seconds_of_day % 3600 / 60;
    const char text[] = {
        static_cast<char>('0' + month / 10), static_cast<char>('0' + month % 10), '-',
        static_cast<char>('0' + day / 10),   static_cast<char>('0' + day % 10),   ' ',
        static_cast<char>('0' + hour / 10),  static_cast<char>('0' + hour % 10),  ':',
        static_cast<char>('0' + minute / 10), static_cast<char>('0' + minute % 10), '\0',
    };
    detail::copy_text(out, out_len, text);
}

// 两版行情画到屏幕上是否完全相同：逐格比较格式化后的文字，再比较页眉的行情时间。
// 相同就不必重画；休市期间屏幕因此保持静止。
inline bool panel_text_equal(const Quote a[kRowCount], const Quote b[kRowCount])
{
    for (int row = 0; row < kRowCount; ++row) {
        if (!row_text_equal(a[row], b[row])) {
            return false;
        }
    }
    char left[kAsOfTextBytes];
    char right[kAsOfTextBytes];
    format_as_of(latest_quote_time(a), left, sizeof(left));
    format_as_of(latest_quote_time(b), right, sizeof(right));
    return strcmp(left, right) == 0;
}

// ---------------------------------------------------------------- 数据源健康度
// 辅助数据源连续失败后降低尝试频率；腾讯是主干源，每轮都必须尝试，不使用这个策略。
struct SourceHealth {
    uint8_t failures = 0;
    uint8_t skipped = 0;
};

constexpr bool source_fetch_due(const SourceHealth &health)
{
    return health.failures < kSourceSkipFailureThreshold ||
           health.skipped >= kSourceSkipCycles;
}

// 记录一轮的结果：attempted=false 表示这一轮按策略跳过了该源。
inline void source_record_cycle(SourceHealth *health, bool attempted, bool succeeded)
{
    if (!health) {
        return;
    }
    if (!attempted) {
        if (health->skipped < UINT8_MAX) {
            ++health->skipped;
        }
        return;
    }
    health->skipped = 0;
    if (succeeded) {
        health->failures = 0;
    } else if (health->failures < UINT8_MAX) {
        ++health->failures;
    }
}

// ---------------------------------------------------------------- 刷新节奏
constexpr uint32_t refresh_interval_seconds(uint8_t consecutive_failures)
{
    return consecutive_failures >= kBackoffFailureThreshold ? kBackoffRefreshSeconds
                                                           : kRefreshSeconds;
}

// 时间一律用开机秒数：对时造成的系统时间跳变不会打乱刷新节奏。
constexpr bool refresh_due(bool requested_before,
                           uint32_t last_request_seconds,
                           uint32_t now_seconds,
                           uint8_t consecutive_failures)
{
    return !requested_before || now_seconds < last_request_seconds ||
           now_seconds - last_request_seconds >=
               refresh_interval_seconds(consecutive_failures);
}

// 手动刷新（BOOT 键、从设置进入）不受每分钟节奏限制，但两次请求至少间隔这么久，
// 连按按键不会连续打行情接口。
inline constexpr uint32_t kManualRefreshMinSeconds = 10;

constexpr bool manual_refresh_allowed(bool requested_before,
                                      uint32_t last_request_seconds,
                                      uint32_t now_seconds)
{
    return !requested_before || now_seconds < last_request_seconds ||
           now_seconds - last_request_seconds >= kManualRefreshMinSeconds;
}

// 服务器渲染的位图只做兜底：本轮直连行情失败时才去下载，且两次下载至少间隔这么久
// （与服务器的出图周期一致）。直连正常时完全不访问位图地址。
inline constexpr uint32_t kBitmapFallbackSeconds = 5 * 60;

constexpr bool bitmap_fallback_due(bool live_update_succeeded,
                                   bool attempted_before,
                                   uint32_t last_attempt_seconds,
                                   uint32_t now_seconds)
{
    return !live_update_succeeded &&
           (!attempted_before || now_seconds < last_attempt_seconds ||
            now_seconds - last_attempt_seconds >= kBitmapFallbackSeconds);
}

constexpr uint32_t age_seconds(uint32_t since_seconds, uint32_t now_seconds)
{
    return now_seconds >= since_seconds ? now_seconds - since_seconds : 0;
}

// 主页显示实时面板还是服务器位图：有实时行情就优先显示；只有实时行情已经
// 超过 kLiveStaleSeconds 没刷新、而位图读取得更晚时，才让位给位图。
constexpr bool prefer_live_view(bool live_has_rows,
                                uint32_t live_age_seconds,
                                bool bitmap_available,
                                bool bitmap_read_later)
{
    return live_has_rows &&
           (!bitmap_available || live_age_seconds < kLiveStaleSeconds || !bitmap_read_later);
}

constexpr bool live_view_fresh(uint32_t live_age_seconds)
{
    return live_age_seconds < kLiveFreshSeconds;
}

static_assert(refresh_interval_seconds(0) == kRefreshSeconds,
              "healthy refresh interval is one minute");
static_assert(refresh_interval_seconds(kBackoffFailureThreshold) == kBackoffRefreshSeconds,
              "repeated failures back off to five minutes");
static_assert(refresh_due(false, 0, 0, 0), "first refresh is always due");
static_assert(!refresh_due(true, 100, 159, 0) && refresh_due(true, 100, 160, 0),
              "refresh becomes due exactly one minute after the last request");
static_assert(manual_refresh_allowed(false, 0, 0) && !manual_refresh_allowed(true, 100, 109) &&
                  manual_refresh_allowed(true, 100, 110),
              "manual refreshes are spaced at least ten seconds apart");
static_assert(!bitmap_fallback_due(true, false, 0, 0),
              "a healthy live panel never downloads the server bitmap");
static_assert(bitmap_fallback_due(false, false, 0, 0) &&
                  !bitmap_fallback_due(false, true, 100, 100 + kBitmapFallbackSeconds - 1) &&
                  bitmap_fallback_due(false, true, 100, 100 + kBitmapFallbackSeconds),
              "the bitmap fallback is throttled to the server render period");
static_assert(prefer_live_view(true, kLiveStaleSeconds, false, false),
              "stale live rows still beat an empty screen");
static_assert(!prefer_live_view(true, kLiveStaleSeconds, true, true),
              "a newer server bitmap replaces a stale live panel");

} // namespace market_panel
