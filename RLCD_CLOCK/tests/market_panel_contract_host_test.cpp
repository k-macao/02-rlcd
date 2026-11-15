// 校验行情数据面板的解析、合并、数字格式与刷新节奏。
//
// 输入是 device_brief/fixtures/ 里留档的四个行情接口实测响应，期望值来自
// device_brief/fixtures_tool.py（服务器渲染器的同一套实现），保证设备自绘面板与
// 服务器位图逐字相同。fixtures 目录可由第一个命令行参数或
// MARKET_PANEL_FIXTURE_DIR 宏指定，默认按「在 RLCD_CLOCK/ 下运行」推断。
#include "market_panel_contract.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

#ifndef MARKET_PANEL_FIXTURE_DIR
#define MARKET_PANEL_FIXTURE_DIR "../device_brief/fixtures"
#endif

namespace {

using market_panel::Quote;
using market_panel::SourceQuotes;

std::string g_fixture_dir = MARKET_PANEL_FIXTURE_DIR;

std::string read_fixture(const char *name)
{
    const std::string path = g_fixture_dir + "/" + name;
    FILE *file = fopen(path.c_str(), "rb");
    if (!file) {
        fprintf(stderr, "cannot open fixture %s\n", path.c_str());
        assert(file);
    }
    std::string data;
    char chunk[4096];
    size_t got = 0;
    while ((got = fread(chunk, 1, sizeof(chunk), file)) > 0) {
        data.append(chunk, got);
    }
    fclose(file);
    assert(!data.empty());
    return data;
}

std::vector<std::string> split(const std::string &text, char separator)
{
    std::vector<std::string> parts;
    size_t start = 0;
    for (;;) {
        const size_t pos = text.find(separator, start);
        if (pos == std::string::npos) {
            parts.push_back(text.substr(start));
            break;
        }
        parts.push_back(text.substr(start, pos - start));
        start = pos + 1;
    }
    return parts;
}

std::vector<std::string> lines_of(const std::string &text)
{
    std::vector<std::string> lines;
    for (const std::string &line : split(text, '\n')) {
        if (!line.empty()) {
            lines.push_back(line);
        }
    }
    return lines;
}

bool near(double actual, double expected)
{
    const double scale = fabs(expected) > 1.0 ? fabs(expected) : 1.0;
    return fabs(actual - expected) <= scale * 1e-9;
}

struct Sources {
    SourceQuotes tencent;
    SourceQuotes sina;
    SourceQuotes naver_kr;
    SourceQuotes naver_jp;
};

Sources parse_fixtures()
{
    Sources sources;
    const std::string tencent = read_fixture("tencent_quotes.txt");
    const std::string sina = read_fixture("sina_hk.txt");
    const std::string naver_kr = read_fixture("naver_kr.json");
    const std::string naver_jp = read_fixture("naver_jp.json");
    assert(market_panel::parse_tencent(tencent.data(), tencent.size(), &sources.tencent));
    assert(market_panel::parse_sina_hk(sina.data(), sina.size(), &sources.sina));
    assert(market_panel::parse_naver(naver_kr.data(), naver_kr.size(), &sources.naver_kr));
    assert(market_panel::parse_naver(naver_jp.data(), naver_jp.size(), &sources.naver_jp));
    return sources;
}

void test_row_catalog_matches_expected_file()
{
    const std::vector<std::string> lines = lines_of(read_fixture("expected_cells.txt"));
    assert(lines.size() == static_cast<size_t>(market_panel::kRowCount) + 2);
    for (int row = 0; row < market_panel::kRowCount; ++row) {
        const std::vector<std::string> cells = split(lines[row], '|');
        assert(cells.size() == 8);
        assert(cells[0] == market_panel::kRows[row].key);
        assert(cells[1] == market_panel::market_label(market_panel::kRows[row].market));
        assert(cells[2] == market_panel::kRows[row].name);
    }
    // 市场顺序固定为 A、港、美、韩、日，且同一市场的行相邻。
    int previous = -1;
    for (const market_panel::RowSpec &spec : market_panel::kRows) {
        const int market = static_cast<int>(spec.market);
        assert(market == previous || market == previous + 1);
        previous = market;
    }
    assert(previous == market_panel::kMarketCount - 1);
    assert(strcmp(market_panel::kMarketLabels[0], "A") == 0);
    assert(strcmp(market_panel::kMarketLabels[4], "日") == 0);
}

void test_parses_tencent_fixture()
{
    const Sources sources = parse_fixtures();
    const Quote &shanghai = sources.tencent.rows[market_panel::kRowSh000001];
    assert(shanghai.valid && near(shanghai.last, 3634.35) && near(shanghai.pct, -0.59));
    // 472293657 手 → 股；77061417 万元 → 元
    assert(near(shanghai.volume, 47229365700.0));
    assert(near(shanghai.turnover, 770614170000.0));

    const Quote &hsi = sources.tencent.rows[market_panel::kRowHkHsi];
    assert(hsi.valid && near(hsi.last, 23637.6) && near(hsi.pct, -1.55));
    // 腾讯港股指数没有成交股数，[36][37] 都是万港元。
    assert(hsi.volume == 0.0 && near(hsi.turnover, 195857007140.0));

    const Quote &dow = sources.tencent.rows[market_panel::kRowUsDji];
    assert(dow.valid && near(dow.last, 52347.55) && near(dow.pct, 0.10));
    // 美股指数只取成交股数，丢弃「股数×点位」的无意义成交额。
    assert(near(dow.volume, 645244111.0) && dow.turnover == 0.0);

    const Quote &nikkei = sources.tencent.rows[market_panel::kRowN225];
    assert(nikkei.valid && near(nikkei.last, 61142.13) && near(nikkei.pct, 0.53));
    assert(nikkei.volume == 0.0 && nikkei.turnover == 0.0);
    assert(sources.tencent.rows[market_panel::kRowKospi].valid);
    assert(sources.tencent.rows[market_panel::kRowKosdaq].valid);
    // 腾讯不提供 TOPIX（gzTPX 是坏数据，面板不请求它）。
    assert(!sources.tencent.rows[market_panel::kRowTopx].valid);
}

void test_parses_sina_and_naver_fixtures()
{
    const Sources sources = parse_fixtures();
    const Quote &hsi = sources.sina.rows[market_panel::kRowHkHsi];
    assert(hsi.valid && near(hsi.last, 23637.6) && near(hsi.pct, -1.55));
    assert(near(hsi.volume, 11525719783.0) && near(hsi.turnover, 195857007140.0));
    const Quote &tech = sources.sina.rows[market_panel::kRowHkHstech];
    assert(tech.valid && near(tech.volume, 1516516927.0));
    assert(!sources.sina.rows[market_panel::kRowSh000001].valid);

    const Quote &kospi = sources.naver_kr.rows[market_panel::kRowKospi];
    assert(kospi.valid && kospi.closed);
    assert(near(kospi.last, 5290.53) && near(kospi.pct, -0.98));
    assert(near(kospi.volume, 247829000.0) && near(kospi.turnover, 16718835000000.0));
    const Quote &kosdaq = sources.naver_kr.rows[market_panel::kRowKosdaq];
    assert(kosdaq.valid && near(kosdaq.last, 699.66) && near(kosdaq.pct, 0.45));
    assert(near(kosdaq.volume, 455458000.0) && near(kosdaq.turnover, 5790471000000.0));
    assert(!sources.naver_kr.rows[market_panel::kRowN225].valid);

    const Quote &nikkei = sources.naver_jp.rows[market_panel::kRowN225];
    assert(nikkei.valid && nikkei.closed);
    assert(near(nikkei.last, 61142.35) && near(nikkei.pct, 0.53));
    // 日本指数只取成交量；成交额字段不可信，必须留空。
    assert(near(nikkei.volume, 1785916000.0) && nikkei.turnover == 0.0);
    const Quote &topix = sources.naver_jp.rows[market_panel::kRowTopx];
    assert(topix.valid && near(topix.last, 3941.57) && near(topix.pct, 0.11));
    assert(near(topix.volume, 2741127000.0) && topix.turnover == 0.0);
}

void test_merged_fixture_matches_server_renderer_text()
{
    const Sources sources = parse_fixtures();
    Quote rows[market_panel::kRowCount];
    market_panel::merge_sources(sources.tencent, sources.sina, sources.naver_kr,
                                sources.naver_jp, rows);
    assert(market_panel::valid_row_count(rows) == market_panel::kRowCount);

    const std::vector<std::string> lines = lines_of(read_fixture("expected_cells.txt"));
    for (int row = 0; row < market_panel::kRowCount; ++row) {
        const std::vector<std::string> cells = split(lines[row], '|');
        market_panel::RowText text;
        market_panel::format_row(rows[row], &text);
        if (cells[3] != text.price || cells[4] != text.pct ||
            cells[5] != text.volume || cells[6] != text.turnover ||
            cells[7] != std::to_string(static_cast<long long>(rows[row].time))) {
            fprintf(stderr, "row %d mismatch: %s %s %s %s %lld\n", row, text.price,
                    text.pct, text.volume, text.turnover,
                    static_cast<long long>(rows[row].time));
            assert(false);
        }
    }
    // 页眉右侧：全表最新一笔行情的北京时间；页脚左侧：沪深两市成交额。
    char as_of[market_panel::kAsOfTextBytes];
    market_panel::format_as_of(market_panel::latest_quote_time(rows), as_of, sizeof(as_of));
    assert(lines[market_panel::kRowCount] == std::string("header|") + as_of);
    char footer[48];
    market_panel::format_two_market(rows, footer, sizeof(footer));
    assert(lines.back() == std::string("footer|") + footer);
}

// 各数据源的时间写法 → UTC 纪元秒，期望值由 Python zoneinfo 算出（含美国夏令时切换日）。
void test_time_cases_match_server_renderer()
{
    using namespace market_panel::detail;
    const std::vector<std::string> lines = lines_of(read_fixture("time_cases.txt"));
    int checked = 0;
    for (const std::string &line : lines) {
        const std::vector<std::string> parts = split(line, '|');
        assert(parts.size() == 3);
        const std::string &kind = parts[0];
        const char *begin = parts[1].data();
        const char *end = begin + parts[1].size();
        int64_t epoch = 0;
        CivilTime civil;
        if (kind == "compact_cn") {
            if (parse_compact_datetime(begin, end, &civil)) {
                epoch = civil_to_epoch(civil, kChinaUtcOffsetSeconds);
            }
        } else if (kind == "slash_cn") {
            if (parse_separated_datetime(begin, end, '/', &civil)) {
                epoch = civil_to_epoch(civil, kChinaUtcOffsetSeconds);
            }
        } else if (kind == "dash_cn") {
            if (parse_separated_datetime(begin, end, '-', &civil)) {
                epoch = civil_to_epoch(civil, kChinaUtcOffsetSeconds);
            }
        } else if (kind == "dash_us") {
            if (parse_separated_datetime(begin, end, '-', &civil)) {
                epoch = civil_to_epoch(civil, us_eastern_utc_offset_seconds(civil));
            }
        } else {
            assert(kind == "iso");
            (void)parse_iso_datetime(begin, end, &epoch);
        }
        if (epoch < 0) {
            epoch = 0;
        }
        if (parts[2] != std::to_string(static_cast<long long>(epoch))) {
            fprintf(stderr, "time mismatch: %s -> %lld\n", line.c_str(),
                    static_cast<long long>(epoch));
            assert(false);
        }
        ++checked;
    }
    assert(checked > 50);

    // 日历换算往返，以及页眉文字的边界。
    for (int64_t days : {int64_t{0}, int64_t{59}, int64_t{19782}, int64_t{20769},
                         int64_t{20770}, int64_t{47481}}) {
        int year = 0;
        int month = 0;
        int day = 0;
        civil_from_days(days, &year, &month, &day);
        assert(days_from_civil(year, month, day) == days);
    }
    static_assert(days_from_civil(1970, 1, 1) == 0, "epoch day");
    static_assert(days_from_civil(2000, 3, 1) == 11017, "leap-century day");
    static_assert(weekday_from_days(0) == 4, "1970-01-01 was a Thursday");
    static_assert(nth_sunday(2026, 3, 2) == 8 && nth_sunday(2026, 11, 1) == 1,
                  "US DST switch days in 2026");
    char text[market_panel::kAsOfTextBytes];
    market_panel::format_as_of(0, text, sizeof(text));
    assert(strcmp(text, "--") == 0);
    market_panel::format_as_of(1794604559, text, sizeof(text)); // 美东 11-13 16:15:59
    assert(strcmp(text, "11-14 05:15") == 0);
    market_panel::format_as_of(1767196799, text, sizeof(text)); // 北京 2025-12-31 23:59:59
    assert(strcmp(text, "12-31 23:59") == 0);
    market_panel::format_as_of(1767196800, text, sizeof(text)); // 北京 2026-01-01 00:00:00
    assert(strcmp(text, "01-01 00:00") == 0);
    char tiny[4] = {'x', 'x', 'x', 'x'};
    market_panel::format_as_of(1794604559, tiny, sizeof(tiny));
    assert(strcmp(tiny, "11-") == 0);
}

void test_format_cases_match_server_renderer()
{
    const std::vector<std::string> lines = lines_of(read_fixture("format_cases.txt"));
    int checked = 0;
    for (const std::string &line : lines) {
        const std::vector<std::string> parts = split(line, '|');
        assert(parts.size() == 3);
        const double value = strtod(parts[1].c_str(), nullptr);
        char out[market_panel::kCellTextBytes];
        if (parts[0] == "price") {
            market_panel::format_price(value, out, sizeof(out));
        } else if (parts[0] == "pct") {
            market_panel::format_pct(value, out, sizeof(out));
        } else {
            assert(parts[0] == "cn");
            market_panel::format_cn_number(value, out, sizeof(out));
        }
        if (parts[2] != out) {
            fprintf(stderr, "format mismatch: %s -> %s (expected %s)\n",
                    line.c_str(), out, parts[2].c_str());
            assert(false);
        }
        ++checked;
    }
    assert(checked > 60);
    char out[market_panel::kCellTextBytes];
    market_panel::format_price(NAN, out, sizeof(out));
    assert(strcmp(out, "--") == 0);
    market_panel::format_pct(INFINITY, out, sizeof(out));
    assert(strcmp(out, "--") == 0);
    market_panel::format_cn_number(NAN, out, sizeof(out));
    assert(strcmp(out, "--") == 0);
    // 输出缓冲区过小时只截断，不越界。
    char tiny[4] = {'x', 'x', 'x', 'x'};
    market_panel::format_price(61142.35, tiny, sizeof(tiny));
    assert(tiny[3] == '\0');
    market_panel::format_cn_number(1.655668e12, tiny, sizeof(tiny));
    assert(tiny[3] == '\0');
}

void test_merge_rules()
{
    SourceQuotes tencent;
    SourceQuotes sina;
    SourceQuotes naver_kr;
    SourceQuotes naver_jp;
    Quote rows[market_panel::kRowCount];

    // 港股：腾讯缺行时整行用新浪；腾讯在时新浪只补成交股数。
    sina.rows[market_panel::kRowHkHsi] = {true, 23000.0, -1.0, 1.1e10, 1.9e11, false};
    market_panel::merge_sources(tencent, sina, naver_kr, naver_jp, rows);
    assert(rows[market_panel::kRowHkHsi].valid && near(rows[market_panel::kRowHkHsi].last, 23000.0));
    tencent.rows[market_panel::kRowHkHsi] = {true, 23100.0, -0.5, 0.0, 2.0e11, false};
    market_panel::merge_sources(tencent, sina, naver_kr, naver_jp, rows);
    assert(near(rows[market_panel::kRowHkHsi].last, 23100.0));
    assert(near(rows[market_panel::kRowHkHsi].volume, 1.1e10));
    assert(near(rows[market_panel::kRowHkHsi].turnover, 2.0e11));
    tencent.rows[market_panel::kRowHkHsi].turnover = 0.0;
    market_panel::merge_sources(tencent, sina, naver_kr, naver_jp, rows);
    assert(near(rows[market_panel::kRowHkHsi].turnover, 1.9e11));

    // 韩国：Naver 优先；Naver 失败时退回腾讯点位（没有成交量）。
    tencent.rows[market_panel::kRowKospi] = {true, 5290.0, -1.0, 0.0, 0.0, false};
    market_panel::merge_sources(tencent, sina, naver_kr, naver_jp, rows);
    assert(rows[market_panel::kRowKospi].valid && rows[market_panel::kRowKospi].volume == 0.0);
    naver_kr.rows[market_panel::kRowKospi] = {true, 5291.0, -0.9, 2.4e8, 1.6e13, false};
    market_panel::merge_sources(tencent, sina, naver_kr, naver_jp, rows);
    assert(near(rows[market_panel::kRowKospi].last, 5291.0));
    assert(near(rows[market_panel::kRowKospi].volume, 2.4e8));

    // 日经：盘中腾讯实时点位 + Naver 成交量。
    tencent.rows[market_panel::kRowN225] = {true, 61500.0, 1.1, 0.0, 0.0, false, 2000};
    naver_jp.rows[market_panel::kRowN225] = {true, 61400.0, 0.9, 9.0e8, 0.0, false, 1100};
    market_panel::merge_sources(tencent, sina, naver_kr, naver_jp, rows);
    assert(near(rows[market_panel::kRowN225].last, 61500.0));
    assert(near(rows[market_panel::kRowN225].pct, 1.1));
    assert(near(rows[market_panel::kRowN225].volume, 9.0e8));
    // 行情时间跟着点位走：盘中是腾讯的实时时间，不是 Naver 延迟行情的时间。
    assert(rows[market_panel::kRowN225].time == 2000);
    // 收盘后整行用 Naver 的官方收盘价。
    naver_jp.rows[market_panel::kRowN225].closed = true;
    market_panel::merge_sources(tencent, sina, naver_kr, naver_jp, rows);
    assert(near(rows[market_panel::kRowN225].last, 61400.0));
    assert(rows[market_panel::kRowN225].time == 1100);
    // 只统计有效行的时间；全表没有时间时返回 0。
    assert(market_panel::latest_quote_time(rows) == 1100);
    Quote untimed[market_panel::kRowCount];
    untimed[0] = {true, 1.0, 0.0, 0.0, 0.0, false, 0};
    untimed[1].time = 99999; // 无效行的时间不算数
    assert(market_panel::latest_quote_time(untimed) == 0);
    // 盘中两者偏差超过 3%：腾讯环球指数源异常，改信 Naver。
    naver_jp.rows[market_panel::kRowN225].closed = false;
    tencent.rows[market_panel::kRowN225].last = 31000.0;
    market_panel::merge_sources(tencent, sina, naver_kr, naver_jp, rows);
    assert(near(rows[market_panel::kRowN225].last, 61400.0));
    // Naver 失败时只剩腾讯点位。
    naver_jp.rows[market_panel::kRowN225] = Quote{};
    market_panel::merge_sources(tencent, sina, naver_kr, naver_jp, rows);
    assert(near(rows[market_panel::kRowN225].last, 31000.0));
    assert(rows[market_panel::kRowN225].volume == 0.0);

    // TOPIX 只信 Naver；A 股与美股只用腾讯。
    assert(!rows[market_panel::kRowTopx].valid);
    assert(!rows[market_panel::kRowSh000001].valid);
    assert(!rows[market_panel::kRowUsDji].valid);
}

void test_rejects_malformed_responses()
{
    SourceQuotes out;
    assert(!market_panel::parse_tencent(nullptr, 0, &out));
    assert(!market_panel::parse_tencent("", 0, &out));
    assert(!market_panel::parse_tencent("x", 1, nullptr));
    const char html[] = "<html><body>502 Bad Gateway v_sh000001</body></html>";
    assert(!market_panel::parse_tencent(html, sizeof(html) - 1, &out));
    assert(!market_panel::parse_sina_hk(html, sizeof(html) - 1, &out));
    assert(!market_panel::parse_naver(html, sizeof(html) - 1, &out));

    // 未知代码、字段不足、点位非正、涨跌幅离谱的行都要丢弃。
    const char unknown[] = "v_sh999999=\"1~x~999999~1.0~1.0\";\n";
    assert(!market_panel::parse_tencent(unknown, sizeof(unknown) - 1, &out));
    const char short_gz[] = "v_gzN225=\"N225~x~2026-11-13 14:30:01~61142.13\";\n";
    assert(!market_panel::parse_tencent(short_gz, sizeof(short_gz) - 1, &out));
    const char zero_gz[] = "v_gzN225=\"N225~x~t~0.00~0~0~AP~\";\n";
    assert(!market_panel::parse_tencent(zero_gz, sizeof(zero_gz) - 1, &out));
    const char wild_gz[] = "v_gzN225=\"N225~x~t~61142.13~1~88.5~AP~\";\n";
    assert(!market_panel::parse_tencent(wild_gz, sizeof(wild_gz) - 1, &out));
    const char text_gz[] = "v_gzN225=\"N225~x~t~abc~1~0.5~AP~\";\n";
    assert(!market_panel::parse_tencent(text_gz, sizeof(text_gz) - 1, &out));
    const char good_gz[] = "v_gzN225=\"N225~x~t~61,142.13~1~-0.50~AP~\";\n";
    assert(market_panel::parse_tencent(good_gz, sizeof(good_gz) - 1, &out));
    assert(near(out.rows[market_panel::kRowN225].last, 61142.13));
    assert(near(out.rows[market_panel::kRowN225].pct, -0.5));

    // Naver：缺 Raw 字段或数字非法时丢弃该项。
    const char no_raw[] = "{\"datas\":[{\"itemCode\":\"KOSPI\",\"closePrice\":\"5,290.53\"}]}";
    assert(!market_panel::parse_naver(no_raw, sizeof(no_raw) - 1, &out));
    const char bad_raw[] =
        "{\"datas\":[{\"itemCode\":\"KOSPI\",\"closePriceRaw\":\"nan\","
        "\"fluctuationsRatioRaw\":\"0.1\"}]}";
    assert(!market_panel::parse_naver(bad_raw, sizeof(bad_raw) - 1, &out));
    // 相邻两项的字段不能串行：第一项缺涨跌幅时不能借用第二项的。
    const char adjacent[] =
        "{\"datas\":[{\"itemCode\":\"KOSPI\",\"closePriceRaw\":\"5290.53\"},"
        "{\"itemCode\":\"KOSDAQ\",\"closePriceRaw\":\"699.66\","
        "\"fluctuationsRatioRaw\":\"0.45\",\"accumulatedTradingVolumeRaw\":\"455458000\"}]}";
    assert(market_panel::parse_naver(adjacent, sizeof(adjacent) - 1, &out));
    assert(!out.rows[market_panel::kRowKospi].valid);
    assert(out.rows[market_panel::kRowKosdaq].valid);
    assert(near(out.rows[market_panel::kRowKosdaq].volume, 455458000.0));
    assert(!out.rows[market_panel::kRowKosdaq].closed);
}

// 响应在任意位置被截断（下载中断）时解析不能越界；用 ASan/UBSan 跑这一项最有意义。
void test_truncated_responses_never_overrun()
{
    const char *names[] = {"tencent_quotes.txt", "sina_hk.txt", "naver_kr.json",
                           "naver_jp.json"};
    for (const char *name : names) {
        const std::string full = read_fixture(name);
        for (size_t length = 0; length <= full.size(); ++length) {
            // 复制到恰好 length 字节的堆块，越界读会被 ASan 抓到。
            std::vector<char> exact(full.begin(), full.begin() + length);
            const char *data = exact.empty() ? nullptr : exact.data();
            SourceQuotes out;
            (void)market_panel::parse_tencent(data, length, &out);
            (void)market_panel::parse_sina_hk(data, length, &out);
            (void)market_panel::parse_naver(data, length, &out);
        }
    }
}

void test_carry_over_and_policy()
{
    Quote previous[market_panel::kRowCount];
    uint32_t previous_at[market_panel::kRowCount] = {};
    Quote fresh[market_panel::kRowCount];
    Quote merged[market_panel::kRowCount];
    uint32_t merged_at[market_panel::kRowCount] = {};
    previous[0] = {true, 3600.0, 0.1, 1.0, 1.0, false};
    previous_at[0] = 1000;
    previous[1] = {true, 11000.0, 0.2, 1.0, 1.0, false};
    previous_at[1] = 1000;
    fresh[1] = {true, 11010.0, 0.3, 2.0, 2.0, false};

    // 本轮没取到的行沿用上一轮数值；取到的行换新并记下时间。
    market_panel::carry_over_rows(previous, previous_at, fresh, 1060, merged, merged_at);
    assert(merged[0].valid && near(merged[0].last, 3600.0) && merged_at[0] == 1000);
    assert(merged[1].valid && near(merged[1].last, 11010.0) && merged_at[1] == 1060);
    assert(!merged[2].valid && merged_at[2] == 0);
    // 沿用不超过 kRowKeepSeconds。
    market_panel::carry_over_rows(previous, previous_at, fresh,
                                  1000 + market_panel::kRowKeepSeconds - 1, merged, merged_at);
    assert(merged[0].valid);
    market_panel::carry_over_rows(previous, previous_at, fresh,
                                  1000 + market_panel::kRowKeepSeconds, merged, merged_at);
    assert(!merged[0].valid && merged_at[0] == 0);
    // 时间倒退（不应发生）时不沿用旧值。
    market_panel::carry_over_rows(previous, previous_at, fresh, 999, merged, merged_at);
    assert(!merged[0].valid);

    // 刷新节奏：正常每分钟一次，连续失败 3 次后退到 5 分钟。
    assert(market_panel::kRefreshSeconds == 60);
    assert(market_panel::refresh_due(false, 0, 5, 0));
    assert(!market_panel::refresh_due(true, 100, 100, 0));
    assert(!market_panel::refresh_due(true, 100, 159, 2));
    assert(market_panel::refresh_due(true, 100, 160, 2));
    assert(!market_panel::refresh_due(true, 100, 399, 3));
    assert(market_panel::refresh_due(true, 100, 400, 3));
    assert(market_panel::refresh_due(true, 100, 50, 0));
    assert(market_panel::refresh_interval_seconds(255) == market_panel::kBackoffRefreshSeconds);

    // 显示选择：有实时行情就显示；只有实时行情陈旧且位图读取更晚时才让位给位图。
    assert(!market_panel::prefer_live_view(false, 0, false, false));
    assert(market_panel::prefer_live_view(true, 0, true, true));
    assert(market_panel::prefer_live_view(true, market_panel::kLiveStaleSeconds - 1, true, true));
    assert(!market_panel::prefer_live_view(true, market_panel::kLiveStaleSeconds, true, true));
    assert(market_panel::prefer_live_view(true, market_panel::kLiveStaleSeconds, true, false));
    assert(market_panel::prefer_live_view(true, 86400, false, false));
    assert(market_panel::live_view_fresh(0));
    assert(!market_panel::live_view_fresh(market_panel::kLiveFreshSeconds));
    assert(market_panel::age_seconds(100, 160) == 60);
    assert(market_panel::age_seconds(160, 100) == 0);

    // 辅助数据源连续失败 3 轮后，每 10 轮才再试一次；成功一次即恢复每轮都试。
    market_panel::SourceHealth health;
    for (int cycle = 0; cycle < market_panel::kSourceSkipFailureThreshold; ++cycle) {
        assert(market_panel::source_fetch_due(health));
        market_panel::source_record_cycle(&health, true, false);
    }
    for (int cycle = 0; cycle < market_panel::kSourceSkipCycles; ++cycle) {
        assert(!market_panel::source_fetch_due(health));
        market_panel::source_record_cycle(&health, false, false);
    }
    assert(market_panel::source_fetch_due(health));
    market_panel::source_record_cycle(&health, true, false);
    assert(!market_panel::source_fetch_due(health));
    for (int cycle = 0; cycle < market_panel::kSourceSkipCycles; ++cycle) {
        market_panel::source_record_cycle(&health, false, false);
    }
    assert(market_panel::source_fetch_due(health));
    market_panel::source_record_cycle(&health, true, true);
    assert(health.failures == 0 && health.skipped == 0);
    assert(market_panel::source_fetch_due(health));
    market_panel::source_record_cycle(nullptr, true, true);
    health.failures = UINT8_MAX;
    health.skipped = UINT8_MAX;
    market_panel::source_record_cycle(&health, false, false);
    market_panel::source_record_cycle(&health, true, false);
    assert(health.failures == UINT8_MAX && health.skipped == 0);

    // 屏幕内容是否变化：只看格式化后的文字和页眉时间（到分钟）。
    {
        Quote before[market_panel::kRowCount];
        Quote after[market_panel::kRowCount];
        before[0] = {true, 3634.351, -0.591, 4.72e10, 7.706e11, false, 1794557667};
        after[0] = before[0];
        assert(market_panel::panel_text_equal(before, after));
        after[0].last = 3634.354;      // 仍显示 3,634.35
        after[0].volume = 4.721e10;    // 仍显示 472亿
        after[0].time = 1794557670;    // 同一分钟
        after[0].closed = true;        // 不上屏的字段
        assert(market_panel::panel_text_equal(before, after));
        after[0].time = 1794557700;    // 进入下一分钟：页眉时间变了
        assert(!market_panel::panel_text_equal(before, after));
        after[0].time = before[0].time;
        after[0].last = 3634.36;
        assert(!market_panel::panel_text_equal(before, after));
        after[0] = before[0];
        after[0].pct = -0.60;
        assert(!market_panel::panel_text_equal(before, after));
        after[0] = before[0];
        after[0].turnover = 7.707e11;
        assert(!market_panel::panel_text_equal(before, after));
        after[0] = before[0];
        after[5] = {true, 1.0, 0.0, 0.0, 0.0, false, 0}; // 多出一行
        assert(!market_panel::panel_text_equal(before, after));
        after[5] = Quote{};
        after[0].valid = false;
        assert(!market_panel::panel_text_equal(before, after));
    }

    // 手动刷新节流与位图兜底节流（开机秒数回绕时不能卡死）。
    assert(market_panel::manual_refresh_allowed(false, 0, 0));
    assert(!market_panel::manual_refresh_allowed(true, 50, 59));
    assert(market_panel::manual_refresh_allowed(true, 50, 60));
    assert(market_panel::manual_refresh_allowed(true, 50, 10));
    assert(!market_panel::bitmap_fallback_due(true, true, 0, 100000));
    assert(market_panel::bitmap_fallback_due(false, false, 0, 0));
    assert(!market_panel::bitmap_fallback_due(false, true, 1000, 1299));
    assert(market_panel::bitmap_fallback_due(false, true, 1000, 1300));
    assert(market_panel::bitmap_fallback_due(false, true, 1000, 999));

    // 两市成交额：沪深任一缺失就不显示。
    Quote rows[market_panel::kRowCount];
    char footer[48];
    market_panel::format_two_market(rows, footer, sizeof(footer));
    assert(strcmp(footer, "两市成交 --") == 0);
    rows[market_panel::kRowSh000001] = {true, 3634.35, -0.59, 4.7e10, 7.7061417e11, false};
    market_panel::format_two_market(rows, footer, sizeof(footer));
    assert(strcmp(footer, "两市成交 --") == 0);
    rows[market_panel::kRowSz399001] = {true, 10968.79, -2.05, 5.6e10, 8.8505404e11, false};
    market_panel::format_two_market(rows, footer, sizeof(footer));
    assert(strcmp(footer, "两市成交 1.66万亿") == 0);
}

} // namespace

int main(int argc, char **argv)
{
    if (argc > 1) {
        g_fixture_dir = argv[1];
    }
    test_row_catalog_matches_expected_file();
    test_parses_tencent_fixture();
    test_parses_sina_and_naver_fixtures();
    test_merged_fixture_matches_server_renderer_text();
    test_format_cases_match_server_renderer();
    test_time_cases_match_server_renderer();
    test_merge_rules();
    test_rejects_malformed_responses();
    test_truncated_responses_never_overrun();
    test_carry_over_and_policy();
    puts("market_panel_contract_host_test passed");
    return 0;
}
