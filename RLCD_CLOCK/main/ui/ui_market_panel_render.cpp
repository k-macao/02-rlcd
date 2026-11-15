// 行情数据面板的设备端自绘：版面坐标见 ui_market_panel_layout.h，
// 绘制顺序与 device_brief/render_brief.py 的 draw_panel 一一对应。
#include "ui_market_panel_render.h"

#include "ui_market_panel_layout.h"

#include <string.h>

namespace {

using namespace market_panel_layout;

constexpr int kBytesPerRow = kWidth / 8;

static_assert(kMarketPanelBitmapBytes ==
                  static_cast<size_t>(kBytesPerRow) * static_cast<size_t>(kHeight),
              "market panel bitmap must cover the whole 400x300 screen");

class MonoSurface {
public:
    explicit MonoSurface(uint8_t *bits) : bits_(bits) {}

    void clear()
    {
        memset(bits_, 0, kMarketPanelBitmapBytes);
    }

    void set(int x, int y, bool black)
    {
        if (x < 0 || y < 0 || x >= kWidth || y >= kHeight) {
            return;
        }
        uint8_t &byte = bits_[y * kBytesPerRow + x / 8];
        const uint8_t mask = static_cast<uint8_t>(0x80U >> (x % 8));
        if (black) {
            byte = static_cast<uint8_t>(byte | mask);
        } else {
            byte = static_cast<uint8_t>(byte & ~mask);
        }
    }

    // 闭区间矩形，与渲染器的 Sheet.box / Sheet.rule 相同。
    void fill(int x0, int y0, int x1, int y1, bool black)
    {
        for (int y = y0; y <= y1; ++y) {
            for (int x = x0; x <= x1; ++x) {
                set(x, y, black);
            }
        }
    }

    void dotted_rule(int x0, int y, int x1)
    {
        for (int x = x0; x <= x1; x += kSeparatorStep) {
            set(x, y, true);
        }
    }

private:
    uint8_t *bits_;
};

constexpr bool is_digit(char c)
{
    return c >= '0' && c <= '9';
}

// 取下一个 UTF-8 码点并前进；非法字节按单字节跳过并返回 0。
uint32_t next_codepoint(const char **cursor)
{
    const unsigned char *p = reinterpret_cast<const unsigned char *>(*cursor);
    uint32_t value = 0;
    int extra = 0;
    if (p[0] < 0x80) {
        value = p[0];
    } else if ((p[0] & 0xE0) == 0xC0) {
        value = p[0] & 0x1FU;
        extra = 1;
    } else if ((p[0] & 0xF0) == 0xE0) {
        value = p[0] & 0x0FU;
        extra = 2;
    } else if ((p[0] & 0xF8) == 0xF0) {
        value = p[0] & 0x07U;
        extra = 3;
    } else {
        *cursor += 1;
        return 0;
    }
    for (int i = 1; i <= extra; ++i) {
        if ((p[i] & 0xC0) != 0x80) {
            *cursor += 1;
            return 0;
        }
        value = (value << 6) | (p[i] & 0x3FU);
    }
    *cursor += 1 + extra;
    return value;
}

struct TextContext {
    const MarketPanelFonts &fonts;
    MonoSurface *surface = nullptr; // 只量宽度时为空
    int origin_y = 0;
    bool white = false;
    int missing = 0;
};

// 画（或只量）一个字形，返回前进宽度。
int place_glyph(TextContext &context, const lv_font_t *font, uint32_t letter, int pen_x)
{
    lv_font_glyph_dsc_t glyph;
    if (!font || !lv_font_get_glyph_dsc(font, &glyph, letter, 0)) {
        ++context.missing;
        return 0;
    }
    if (context.surface && glyph.bpp == 1 && glyph.box_w > 0 && glyph.box_h > 0) {
        const uint8_t *bitmap = lv_font_get_glyph_bitmap(font, letter);
        if (bitmap) {
            // LVGL 的 1bpp 字形是不按行对齐的连续位流；ofs_y 是字形底边相对基线的偏移。
            const int left = pen_x + glyph.ofs_x;
            const int top = context.origin_y + kTextBaseline - glyph.ofs_y - glyph.box_h;
            uint32_t bit = 0;
            for (int row = 0; row < glyph.box_h; ++row) {
                for (int column = 0; column < glyph.box_w; ++column, ++bit) {
                    if (bitmap[bit >> 3] & (0x80U >> (bit & 7U))) {
                        context.surface->set(left + column, top + row, !context.white);
                    }
                }
            }
        }
    }
    return glyph.adv_w;
}

int place_run(TextContext &context, const lv_font_t *font,
              const char *begin, const char *end, int pen_x)
{
    int advance = 0;
    const char *cursor = begin;
    while (cursor < end && *cursor) {
        const uint32_t letter = next_codepoint(&cursor);
        if (letter == 0) {
            ++context.missing;
            continue;
        }
        advance += place_glyph(context, font, letter, pen_x + advance);
    }
    return advance;
}

// 按数值串分段排版：数值串 Bold，其余 Regular；bold=true 时整段 Bold。
int place_text(TextContext &context, const char *text, bool bold, int pen_x)
{
    if (!text) {
        return 0;
    }
    const char *end = text + strlen(text);
    if (bold) {
        return place_run(context, context.fonts.bold, text, end, pen_x);
    }
    int advance = 0;
    const char *plain_begin = text;
    const char *cursor = text;
    while (cursor < end) {
        const size_t numeric = market_panel_numeric_run_length(cursor);
        if (numeric == 0) {
            (void)next_codepoint(&cursor);
            continue;
        }
        advance += place_run(context, context.fonts.regular, plain_begin, cursor,
                             pen_x + advance);
        advance += place_run(context, context.fonts.bold, cursor, cursor + numeric,
                             pen_x + advance);
        cursor += numeric;
        plain_begin = cursor;
    }
    advance += place_run(context, context.fonts.regular, plain_begin, end, pen_x + advance);
    return advance;
}

class Painter {
public:
    // clear=false 时在已有位图上继续画（给服务器位图补写状态文字）。
    Painter(const MarketPanelFonts &fonts, uint8_t *bitmap, bool clear = true)
        : fonts_(fonts), surface_(bitmap)
    {
        if (clear) {
            surface_.clear();
        }
    }

    MonoSurface &surface()
    {
        return surface_;
    }

    int measure(const char *text, bool bold)
    {
        TextContext context{fonts_};
        const int width = place_text(context, text, bold, 0);
        return width;
    }

    void text(int x, int y, const char *value, bool bold = false, bool white = false)
    {
        TextContext context{fonts_, &surface_, y, white};
        (void)place_text(context, value, bold, x);
        missing_ += context.missing;
    }

    // 右对齐：文字占据 [right - 宽度, right)。
    void text_right(int right, int y, const char *value, bool bold = false,
                    bool white = false)
    {
        text(right - measure(value, bold), y, value, bold, white);
    }

    int missing() const
    {
        return missing_;
    }

private:
    const MarketPanelFonts &fonts_;
    MonoSurface surface_;
    int missing_ = 0;
};

bool is_empty_cell(const char *text)
{
    return strcmp(text, market_panel::kEmptyCell) == 0;
}

} // namespace

size_t market_panel_numeric_run_length(const char *text)
{
    if (!text) {
        return 0;
    }
    size_t i = 0;
    if (text[i] == '+' || text[i] == '-') {
        ++i;
    }
    if (!is_digit(text[i])) {
        return 0;
    }
    while (is_digit(text[i])) {
        ++i;
    }
    for (;;) {
        const char separator = text[i];
        const bool joins = separator == '.' || separator == ',' || separator == ':' ||
                           separator == '/' || separator == '-';
        if (!joins || !is_digit(text[i + 1])) {
            break;
        }
        ++i;
        while (is_digit(text[i])) {
            ++i;
        }
    }
    if (text[i] == '%') {
        ++i;
    }
    return i;
}

int market_panel_text_width(const MarketPanelFonts &fonts,
                            const char *text,
                            bool bold,
                            int *missing)
{
    TextContext context{fonts};
    const int width = place_text(context, text, bold, 0);
    if (missing) {
        *missing += context.missing;
    }
    return width;
}

bool draw_market_panel_status(const MarketPanelFonts &fonts,
                              const char *status,
                              uint8_t *bitmap,
                              size_t bitmap_len,
                              MarketPanelRenderStats *stats)
{
    if (stats) {
        *stats = MarketPanelRenderStats{};
    }
    if (!bitmap || bitmap_len != kMarketPanelBitmapBytes || !fonts.regular || !fonts.bold) {
        return false;
    }
    Painter painter(fonts, bitmap, false);
    painter.surface().fill(kFooterStatusLeft, kFooterTextY, kFooterStatusRight - 1, kHeight - 1,
                           false);
    if (status && status[0]) {
        painter.text_right(kFooterStatusRight, kFooterTextY, status);
    }
    if (stats) {
        stats->missing_glyphs = painter.missing();
    }
    return true;
}

bool render_market_panel(const MarketPanelFrame &frame,
                         const MarketPanelFonts &fonts,
                         uint8_t *bitmap,
                         size_t bitmap_len,
                         MarketPanelRenderStats *stats)
{
    if (stats) {
        *stats = MarketPanelRenderStats{};
    }
    if (!bitmap || bitmap_len != kMarketPanelBitmapBytes || !fonts.regular || !fonts.bold) {
        return false;
    }
    Painter painter(fonts, bitmap);
    MonoSurface &surface = painter.surface();

    // 顶栏：黑底白字，左标题、右行情时间。
    surface.fill(0, 0, kWidth - 1, kHeaderBarBottom, true);
    painter.text(kPageLeft, kHeaderTextY, kTitleText, true, true);
    char as_of[market_panel::kAsOfTextBytes];
    market_panel::format_as_of(market_panel::latest_quote_time(frame.rows), as_of,
                               sizeof(as_of));
    painter.text_right(kPageRight + 1, kHeaderTextY, as_of, false, true);

    // 列标题。
    painter.text(kNameX, kColumnHeaderY, kColumnName);
    painter.text_right(kPriceRight, kColumnHeaderY, kColumnPrice);
    painter.text_right(kPctRight, kColumnHeaderY, kColumnPct);
    painter.text_right(kVolumeRight, kColumnHeaderY, kColumnVolume);
    painter.text_right(kTurnoverRight, kColumnHeaderY, kColumnTurnover);
    surface.fill(kTagX0, kColumnRuleY, kPageRight, kColumnRuleY, true);

    // 市场色块（黑底白字）与组间点线。
    for (int first = 0; first < market_panel::kRowCount;) {
        const market_panel::Market market = market_panel::kRows[first].market;
        int count = 1;
        while (first + count < market_panel::kRowCount &&
               market_panel::kRows[first + count].market == market) {
            ++count;
        }
        const TagGeometry geometry = tag_geometry(first, count);
        surface.fill(kTagX0, geometry.block_top, kTagX1, geometry.block_bottom, true);
        const char *label = market_panel::market_label(market);
        const int label_width = painter.measure(label, true);
        painter.text(kTagX0 + (kTagX1 - kTagX0 + 1 - label_width) / 2, geometry.label_y,
                     label, true, true);
        if (first > 0) {
            surface.dotted_rule(kNameX, geometry.separator_y, kPageRight);
        }
        first += count;
    }

    // 行情行：名称 + 四个右对齐数值。「--」不含数字，显式用 Bold。
    for (int row = 0; row < market_panel::kRowCount; ++row) {
        const int y = row_y(row);
        market_panel::RowText cells;
        market_panel::format_row(frame.rows[row], &cells);
        painter.text(kNameX, y, market_panel::kRows[row].name);
        painter.text_right(kPriceRight, y, cells.price, true);
        painter.text_right(kPctRight, y, cells.pct, true);
        painter.text_right(kVolumeRight, y, cells.volume, is_empty_cell(cells.volume));
        painter.text_right(kTurnoverRight, y, cells.turnover,
                           is_empty_cell(cells.turnover));
    }

    // 页脚：左侧两市成交额，右侧状态文字。
    surface.fill(kTagX0, kFooterRuleY, kPageRight, kFooterRuleY, true);
    char footer[48];
    market_panel::format_two_market(frame.rows, footer, sizeof(footer));
    painter.text(kPageLeft, kFooterTextY, footer);
    if (frame.status[0]) {
        painter.text_right(kFooterStatusRight, kFooterTextY, frame.status);
    }

    if (stats) {
        stats->missing_glyphs = painter.missing();
    }
    return true;
}
