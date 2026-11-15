// 校验行情数据面板的设备端自绘：数值串分段、字形覆盖、状态文字，
// 以及与服务器渲染器位图逐像素一致。
//
// 基准图 panel_*.pbm 由 device_brief/fixtures_tool.py 用服务器渲染器生成；
// 这里用同一批 fixtures 行情、同一套版面坐标和 lv_font_conv 生成的设备字库重画一遍，
// 两边任何一处版面、字重或数字格式改动没同步都会在这里暴露。
#include "market_panel_contract.h"
#include "ui_aibrief_page_format.h"
#include "ui_market_panel_layout.h"
#include "ui_market_panel_render.h"
#include "ui_market_panel_status.h"

#include "lvgl.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

#ifndef MARKET_PANEL_FIXTURE_DIR
#define MARKET_PANEL_FIXTURE_DIR "../device_brief/fixtures"
#endif

LV_FONT_DECLARE(market_panel_text_16);
LV_FONT_DECLARE(market_panel_bold_16);

namespace {

std::string g_fixture_dir = MARKET_PANEL_FIXTURE_DIR;
std::string g_dump_dir;

const MarketPanelFonts kFonts = {&market_panel_text_16, &market_panel_bold_16};

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
    return data;
}

// PBM P4：文件头 "P4\n400 300\n" 后面就是 1bpp 位图（MSB first，1 = 黑）。
std::vector<uint8_t> read_golden(const char *name)
{
    const std::string data = read_fixture(name);
    const std::string header = "P4\n400 300\n";
    assert(data.size() == header.size() + kMarketPanelBitmapBytes);
    assert(data.compare(0, header.size(), header) == 0);
    return std::vector<uint8_t>(data.begin() + static_cast<long>(header.size()), data.end());
}

void dump_bitmap(const char *name, const std::vector<uint8_t> &bitmap)
{
    if (g_dump_dir.empty()) {
        return;
    }
    const std::string path = g_dump_dir + "/" + name;
    FILE *file = fopen(path.c_str(), "wb");
    if (!file) {
        return;
    }
    fputs("P4\n400 300\n", file);
    fwrite(bitmap.data(), 1, bitmap.size(), file);
    fclose(file);
}

struct SourceSet {
    bool tencent;
    bool sina;
    bool naver_kr;
    bool naver_jp;
};

MarketPanelFrame fixture_frame(const SourceSet &sources, const char *status)
{
    market_panel::SourceQuotes tencent;
    market_panel::SourceQuotes sina;
    market_panel::SourceQuotes naver_kr;
    market_panel::SourceQuotes naver_jp;
    if (sources.tencent) {
        const std::string text = read_fixture("tencent_quotes.txt");
        assert(market_panel::parse_tencent(text.data(), text.size(), &tencent));
    }
    if (sources.sina) {
        const std::string text = read_fixture("sina_hk.txt");
        assert(market_panel::parse_sina_hk(text.data(), text.size(), &sina));
    }
    if (sources.naver_kr) {
        const std::string text = read_fixture("naver_kr.json");
        assert(market_panel::parse_naver(text.data(), text.size(), &naver_kr));
    }
    if (sources.naver_jp) {
        const std::string text = read_fixture("naver_jp.json");
        assert(market_panel::parse_naver(text.data(), text.size(), &naver_jp));
    }
    MarketPanelFrame frame;
    market_panel::merge_sources(tencent, sina, naver_kr, naver_jp, frame.rows);
    snprintf(frame.status, sizeof(frame.status), "%s", status);
    return frame;
}

int count_black(const std::vector<uint8_t> &bitmap)
{
    int black = 0;
    for (uint8_t byte : bitmap) {
        for (int bit = 0; bit < 8; ++bit) {
            black += (byte >> bit) & 1;
        }
    }
    return black;
}

void expect_same_bitmap(const char *name, const std::vector<uint8_t> &actual,
                        const std::vector<uint8_t> &expected)
{
    assert(actual.size() == expected.size());
    int differing = 0;
    for (int y = 0; y < market_panel_layout::kHeight; ++y) {
        for (int x = 0; x < market_panel_layout::kWidth; ++x) {
            const size_t index = static_cast<size_t>(y) * 50U + static_cast<size_t>(x) / 8U;
            const uint8_t mask = static_cast<uint8_t>(0x80U >> (x % 8));
            if ((actual[index] & mask) != (expected[index] & mask)) {
                if (differing < 20) {
                    fprintf(stderr, "%s: pixel (%d,%d) device=%d server=%d\n", name, x, y,
                            (actual[index] & mask) != 0, (expected[index] & mask) != 0);
                }
                ++differing;
            }
        }
    }
    if (differing != 0) {
        fprintf(stderr, "%s: %d pixels differ from the server bitmap\n", name, differing);
        assert(false);
    }
}

void test_numeric_runs_match_renderer_rule()
{
    // 与 render_brief.py 的 _NUMERIC_RUN = [+-]?\d+(?:[.,:/-]\d+)*%? 相同。
    assert(market_panel_numeric_run_length("3,634.35") == 8);
    assert(market_panel_numeric_run_length("-0.59%") == 6);
    assert(market_panel_numeric_run_length("+0.10%") == 6);
    assert(market_panel_numeric_run_length("0.00%") == 5);
    assert(market_panel_numeric_run_length("472亿") == 3);
    assert(market_panel_numeric_run_length("1.66万亿") == 4);
    assert(market_panel_numeric_run_length("11-14 06:15") == 5);
    assert(market_panel_numeric_run_length("06:15") == 5);
    assert(market_panel_numeric_run_length("12 分钟未更新") == 2);
    assert(market_panel_numeric_run_length("500") == 3);
    assert(market_panel_numeric_run_length("5.5.5") == 5);
    assert(market_panel_numeric_run_length("5%%") == 2);
    assert(market_panel_numeric_run_length("5-") == 1);
    assert(market_panel_numeric_run_length("5.") == 1);
    assert(market_panel_numeric_run_length("-5") == 2);
    // 不是数值串：占位符、单独的符号、汉字和字母。
    assert(market_panel_numeric_run_length("--") == 0);
    assert(market_panel_numeric_run_length("-") == 0);
    assert(market_panel_numeric_run_length("+") == 0);
    assert(market_panel_numeric_run_length("%") == 0);
    assert(market_panel_numeric_run_length(".5") == 0);
    assert(market_panel_numeric_run_length(" 06:15") == 0);
    assert(market_panel_numeric_run_length("标普500") == 0);
    assert(market_panel_numeric_run_length("KOSPI") == 0);
    assert(market_panel_numeric_run_length("") == 0);
    assert(market_panel_numeric_run_length(nullptr) == 0);
}

void expect_covered(const char *text, bool bold)
{
    int missing = 0;
    const int width = market_panel_text_width(kFonts, text, bold, &missing);
    if (missing != 0 || width <= 0) {
        fprintf(stderr, "font coverage: %d glyphs missing in \"%s\"\n", missing, text);
        assert(false);
    }
}

// LVGL 标签整段只用一套字库：占位提示里的每个字都必须在 Regular 字库里。
void expect_covered_by_regular_only(const char *text)
{
    const MarketPanelFonts regular_only = {&market_panel_text_16, &market_panel_text_16};
    int missing = 0;
    (void)market_panel_text_width(regular_only, text, false, &missing);
    if (missing != 0) {
        fprintf(stderr, "regular font misses %d glyphs in \"%s\"\n", missing, text);
        assert(false);
    }
}

void test_fonts_cover_all_panel_text()
{
    using namespace market_panel_layout;
    expect_covered(kTitleText, true);
    for (const char *label : market_panel::kMarketLabels) {
        expect_covered(label, true);
    }
    for (const char *text : {kColumnName, kColumnPrice, kColumnPct, kColumnVolume,
                             kColumnTurnover, kStatusEveryMinute, kStatusLowBattery,
                             kStatusOffline, kBitmapReadPrefix, kPlaceholderText,
                             kPlaceholderHint, market_panel::kTwoMarketLabel}) {
        expect_covered(text, false);
    }
    for (const market_panel::RowSpec &row : market_panel::kRows) {
        expect_covered(row.name, false);
    }
    // 数值串、单位与占位符。
    expect_covered("0123456789", false);
    expect_covered("+1,234,567.89%", false);
    expect_covered("-0.01%", false);
    expect_covered("12-31 23:59", false);
    expect_covered("9,999万", false);
    expect_covered("1.23万亿", false);
    expect_covered(market_panel::kEmptyCell, true);
    char status[kMarketPanelStatusBytes];
    for (uint32_t age : {0U, 180U, 600U, 6000U, 60000U, UINT32_MAX}) {
        for (MarketPanelPause pause : {MarketPanelPause::kNone, MarketPanelPause::kLowBattery,
                                       MarketPanelPause::kOffline}) {
            format_market_panel_status(age, pause, status, sizeof(status));
            expect_covered(status, false);
            // 状态文字不能压到页脚左侧的「两市成交 9,999亿」，也不能超出状态区。
            const int width = market_panel_text_width(kFonts, status, false);
            const int left_end =
                kPageLeft + market_panel_text_width(kFonts, "两市成交 9,999亿", false);
            assert(kFooterStatusRight - width > left_end + 8);
            assert(kFooterStatusRight - width >= kFooterStatusLeft);
        }
    }
    // 页脚左侧最宽的文字也进不了状态区：设备清空状态区时不会擦掉它。
    assert(kPageLeft + market_panel_text_width(kFonts, "两市成交 9,999万亿", false) <
           kFooterStatusLeft);
    expect_covered_by_regular_only(kPlaceholderText);
    expect_covered_by_regular_only(kPlaceholderHint);
    expect_covered_by_regular_only(kBitmapReadPrefix);

    // 字库里没有的字要计入 missing，而不是悄悄画成空白。
    int missing = 0;
    (void)market_panel_text_width(kFonts, "龘", false, &missing);
    assert(missing == 1);
    missing = 0;
    (void)market_panel_text_width(kFonts, "\xE4\xB8", false, &missing); // 截断的 UTF-8
    assert(missing > 0);
}

void test_status_text()
{
    char status[kMarketPanelStatusBytes];
    format_market_panel_status(0, MarketPanelPause::kNone, status, sizeof(status));
    assert(strcmp(status, "每分钟更新") == 0);
    format_market_panel_status(market_panel::kLiveFreshSeconds - 1,
                               MarketPanelPause::kLowBattery, status, sizeof(status));
    assert(strcmp(status, "每分钟更新") == 0);
    format_market_panel_status(market_panel::kLiveFreshSeconds, MarketPanelPause::kNone,
                               status, sizeof(status));
    assert(strcmp(status, "3 分钟未更新") == 0);
    format_market_panel_status(12 * 60 + 59, MarketPanelPause::kNone, status, sizeof(status));
    assert(strcmp(status, "12 分钟未更新") == 0);
    format_market_panel_status(100 * 60, MarketPanelPause::kNone, status, sizeof(status));
    assert(strcmp(status, "100 分钟未更新") == 0);
    format_market_panel_status(UINT32_MAX, MarketPanelPause::kNone, status, sizeof(status));
    assert(strcmp(status, "999 分钟未更新") == 0);
    format_market_panel_status(600, MarketPanelPause::kLowBattery, status, sizeof(status));
    assert(strcmp(status, "低电量 暂停更新") == 0);
    format_market_panel_status(600, MarketPanelPause::kOffline, status, sizeof(status));
    assert(strcmp(status, "离线模式 暂停更新") == 0);
    // 重绘键：文字不变键就不变，文字变了键必须变。
    assert(market_panel_status_key(0, MarketPanelPause::kNone) ==
           market_panel_status_key(179, MarketPanelPause::kNone));
    assert(market_panel_status_key(180, MarketPanelPause::kNone) ==
           market_panel_status_key(239, MarketPanelPause::kNone));
    assert(market_panel_status_key(239, MarketPanelPause::kNone) !=
           market_panel_status_key(240, MarketPanelPause::kNone));
    assert(market_panel_status_key(600, MarketPanelPause::kLowBattery) ==
           market_panel_status_key(6000, MarketPanelPause::kLowBattery));
    assert(market_panel_status_key(600, MarketPanelPause::kLowBattery) !=
           market_panel_status_key(600, MarketPanelPause::kOffline));
    assert(market_panel_status_key(600, MarketPanelPause::kLowBattery) !=
           market_panel_status_key(600, MarketPanelPause::kNone));
    char tiny[4] = {'x', 'x', 'x', 'x'};
    format_market_panel_status(12 * 60, MarketPanelPause::kNone, tiny, sizeof(tiny));
    assert(tiny[3] == '\0');
}

void test_render_matches_server_bitmap()
{
    struct Case {
        const char *golden;
        SourceSet sources;
        const char *status;
    };
    // 与 device_brief/fixtures_tool.py 的 PANEL_IMAGES 同一张表。
    const Case cases[] = {
        {"panel_plain.pbm", {true, true, true, true}, ""},
        {"panel_live.pbm", {true, true, true, true}, "每分钟更新"},
        {"panel_partial.pbm", {true, false, false, false}, "12 分钟未更新"},
    };
    for (const Case &item : cases) {
        const MarketPanelFrame frame = fixture_frame(item.sources, item.status);
        std::vector<uint8_t> bitmap(kMarketPanelBitmapBytes, 0xAA);
        MarketPanelRenderStats stats;
        assert(render_market_panel(frame, kFonts, bitmap.data(), bitmap.size(), &stats));
        assert(stats.missing_glyphs == 0);
        dump_bitmap(item.golden, bitmap);
        expect_same_bitmap(item.golden, bitmap, read_golden(item.golden));
    }
}

// 位图兜底：设备在服务器位图（页脚右侧留白）上补写状态文字。
// 结果必须与服务器直接把同一段文字画进去的位图逐像素相同。
void test_status_overlay_on_server_bitmap()
{
    const std::vector<uint8_t> plain = read_golden("panel_plain.pbm");
    const std::vector<uint8_t> live = read_golden("panel_live.pbm");

    std::vector<uint8_t> bitmap = plain;
    MarketPanelRenderStats stats;
    assert(draw_market_panel_status(kFonts, "每分钟更新", bitmap.data(), bitmap.size(), &stats));
    assert(stats.missing_glyphs == 0);
    expect_same_bitmap("status overlay", bitmap, live);

    // 重写时先清空状态区：旧文字不会残留；空文字只清空。
    assert(draw_market_panel_status(kFonts, "", bitmap.data(), bitmap.size(), &stats));
    expect_same_bitmap("status cleared", bitmap, plain);
    assert(draw_market_panel_status(kFonts, nullptr, bitmap.data(), bitmap.size()));
    expect_same_bitmap("status cleared (null)", bitmap, plain);

    // 「读取 HH:MM」：与 ui_aibrief_page_format.h 的前缀一致，字库齐全，只改动状态区。
    struct tm local = {};
    local.tm_hour = 7;
    local.tm_min = 9;
    char read_status[kMarketPanelStatusBytes] = {};
    format_aibrief_read_indicator(read_status, sizeof(read_status), &local);
    const std::string expected = std::string(market_panel_layout::kBitmapReadPrefix) + "07:09";
    assert(expected == read_status);
    assert(draw_market_panel_status(kFonts, read_status, bitmap.data(), bitmap.size(), &stats));
    assert(stats.missing_glyphs == 0);
    dump_bitmap("panel_bitmap_read.pbm", bitmap);
    using namespace market_panel_layout;
    int changed_inside = 0;
    for (int y = 0; y < kHeight; ++y) {
        for (int x = 0; x < kWidth; ++x) {
            const size_t index = static_cast<size_t>(y) * 50U + static_cast<size_t>(x) / 8U;
            const uint8_t mask = static_cast<uint8_t>(0x80U >> (x % 8));
            if ((bitmap[index] & mask) == (plain[index] & mask)) {
                continue;
            }
            const bool inside = x >= kFooterStatusLeft && x < kFooterStatusRight &&
                                y >= kFooterTextY;
            assert(inside);
            ++changed_inside;
        }
    }
    assert(changed_inside > 100);

    // 参数非法时拒绝绘制，且不写缓冲区。
    std::vector<uint8_t> untouched(kMarketPanelBitmapBytes, 0x5A);
    assert(!draw_market_panel_status(kFonts, "x", nullptr, kMarketPanelBitmapBytes));
    assert(!draw_market_panel_status(kFonts, "x", untouched.data(), untouched.size() - 1));
    assert(!draw_market_panel_status(MarketPanelFonts{}, "x", untouched.data(),
                                     untouched.size()));
    assert(untouched[0] == 0x5A && untouched.back() == 0x5A);
}

void test_render_handles_empty_and_extreme_frames()
{
    // 一行行情都没有：仍然画出完整表格，所有数值格都是「--」。
    MarketPanelFrame empty;
    std::vector<uint8_t> bitmap(kMarketPanelBitmapBytes, 0xFF);
    MarketPanelRenderStats stats;
    assert(render_market_panel(empty, kFonts, bitmap.data(), bitmap.size(), &stats));
    assert(stats.missing_glyphs == 0);
    const int empty_black = count_black(bitmap);
    assert(empty_black > 9000 && empty_black < 40000);
    // 顶栏是黑条，表格区左上角是白底。
    assert(bitmap[0] == 0xFF && bitmap[50U * 23U] == 0xFF);
    assert(bitmap[50U * 30U] == 0x00);

    // 各列取最宽的合理数值（成交量 9,999亿 股、成交额 9,999万亿）也不能互相压住。
    MarketPanelFrame wide;
    for (int row = 0; row < market_panel::kRowCount; ++row) {
        wide.rows[row] = {true, 888888.88, -48.88, 9.999e11, 9.999e15, false, 1794604559};
    }
    snprintf(wide.status, sizeof(wide.status), "%s", "离线模式 暂停更新");
    assert(render_market_panel(wide, kFonts, bitmap.data(), bitmap.size(), &stats));
    assert(stats.missing_glyphs == 0);
    using namespace market_panel_layout;
    market_panel::RowText cells;
    market_panel::format_row(wide.rows[0], &cells);
    assert(strcmp(cells.price, "888,888.88") == 0 && strcmp(cells.pct, "-48.88%") == 0);
    assert(strcmp(cells.volume, "9,999亿") == 0 && strcmp(cells.turnover, "9,999万亿") == 0);
    const int name_end = kNameX + market_panel_text_width(kFonts, "恒生科技", false);
    const int price_left = kPriceRight - market_panel_text_width(kFonts, cells.price, true);
    const int pct_left = kPctRight - market_panel_text_width(kFonts, cells.pct, true);
    const int volume_left =
        kVolumeRight - market_panel_text_width(kFonts, cells.volume, false);
    const int turnover_left =
        kTurnoverRight - market_panel_text_width(kFonts, cells.turnover, false);
    assert(name_end < price_left);
    assert(kPriceRight < pct_left);
    assert(kPctRight < volume_left);
    assert(kVolumeRight < turnover_left);

    // 参数非法时拒绝绘制，且不写缓冲区。
    std::vector<uint8_t> untouched(kMarketPanelBitmapBytes, 0x5A);
    assert(!render_market_panel(empty, kFonts, nullptr, kMarketPanelBitmapBytes));
    assert(!render_market_panel(empty, kFonts, untouched.data(), untouched.size() - 1));
    assert(!render_market_panel(empty, MarketPanelFonts{}, untouched.data(), untouched.size()));
    assert(untouched[0] == 0x5A && untouched.back() == 0x5A);
}

} // namespace

int main(int argc, char **argv)
{
    if (argc > 1) {
        g_fixture_dir = argv[1];
    }
    if (argc > 2) {
        g_dump_dir = argv[2];
    }
    test_numeric_runs_match_renderer_rule();
    test_fonts_cover_all_panel_text();
    test_status_text();
    test_render_matches_server_bitmap();
    test_status_overlay_on_server_bitmap();
    test_render_handles_empty_and_extreme_frames();
    puts("market_panel_view_host_test passed");
    return 0;
}
