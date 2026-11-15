// 构建行情数据面板页（固件的常驻主页），三种画面共用一张 400x300 的 1bpp 位图：
//   1. 设备直连行情接口取到了数据：在本机把面板画出来，页脚写「每分钟更新」；
//   2. 直连失败、但取到了服务器渲染的同款位图：显示位图，页脚写「读取 HH:MM」；
//   3. 两者都没有：显示占位提示。
// 只有行情文字或页脚状态变化时才重画，休市期间屏幕保持静止。
#include "ui_aibrief_page.h"

#include "aibrief_contract.h"
#include "aibrief_state.h"
#include "app_metadata.h"
#include "battery_runtime_state.h"
#include "market_panel_contract.h"
#include "market_panel_state.h"
#include "offline_mode_state.h"
#include "ui_aibrief_page_format.h"
#include "ui_canvas_primitives.h"
#include "ui_fonts.h"
#include "ui_market_panel_layout.h"
#include "ui_market_panel_render.h"
#include "ui_market_panel_status.h"
#include "ui_page_state.h"
#include "ui_widgets.h"

#include <esp_attr.h>
#include <esp_log.h>

#include <cstdint>
#include <ctime>

namespace {
#define MARKET_PANEL_CANVAS_CREATE_FAILED_LOG "market panel canvas create failed"
#define MARKET_PANEL_BUFFER_ALLOC_FAILED_LOG "market panel canvas buffer alloc failed"
#define MARKET_PANEL_LABEL_CREATE_FAILED_LOG "market panel label create failed"
#define MARKET_PANEL_MISSING_GLYPHS_FORMAT "market panel font misses %d glyphs"

constexpr int kPanelX = 0;
constexpr int kPanelY = 0;
constexpr int kEmptyLabelX = 40;
constexpr int kEmptyLabelY = 122;
constexpr int kEmptyLabelW = 320;
constexpr int kEmptyLabelH = 24;
constexpr int kHintLabelX = 40;
constexpr int kHintLabelY = 152;
constexpr int kHintLabelW = 320;
constexpr int kHintLabelH = 20;

enum class PanelView : uint8_t {
    kNone,
    kPlaceholder,
    kLive,
    kBitmap,
};

lv_obj_t *s_panel_canvas;
lv_color_t *s_panel_canvas_buffer;
lv_obj_t *s_panel_empty_label;
lv_obj_t *s_panel_hint_label;
PanelView s_drawn_view;
uint32_t s_drawn_live_version;
uint32_t s_drawn_status_key;
uint32_t s_drawn_bitmap_version;
int s_drawn_read_minute;
bool s_missing_glyphs_logged;
// 只有界面任务使用；做成静态存储，不占界面任务的栈。位图放进 PSRAM。
EXT_RAM_BSS_ATTR uint8_t s_panel_bitmap[kMarketPanelBitmapBytes];
MarketPanelFrame s_panel_frame;

static_assert(aibrief::kWidth == market_panel_layout::kWidth &&
                  aibrief::kHeight == market_panel_layout::kHeight,
              "the server bitmap and the device-rendered panel share one screen size");
static_assert(aibrief::kBitmapBytes == kMarketPanelBitmapBytes,
              "the server bitmap and the device-rendered panel share one buffer");
static_assert(kEmptyLabelW > 0 && kHintLabelH > 0,
              "market panel placeholder labels must have positive size");

MarketPanelFonts panel_fonts()
{
    return {&market_panel_text_16, &market_panel_bold_16};
}

void show_placeholder(bool visible)
{
    set_obj_visible(s_panel_empty_label, visible);
    set_obj_visible(s_panel_hint_label, visible);
}

// 把 1bpp 位图贴到全屏 canvas（1 = 黑）。
void blit_panel_bitmap()
{
    lv_color_t *pixel = s_panel_canvas_buffer;
    const lv_color_t black = lv_color_black();
    const lv_color_t white = lv_color_white();
    for (int y = 0; y < aibrief::kHeight; ++y) {
        const uint8_t *row = s_panel_bitmap + y * aibrief::kBytesPerRow;
        for (int x = 0; x < aibrief::kWidth; ++x) {
            *pixel++ = aibrief::bit_is_set(row, static_cast<uint32_t>(x)) ? black : white;
        }
    }
    lv_obj_invalidate(s_panel_canvas);
}

void note_missing_glyphs(const MarketPanelRenderStats &stats)
{
    if (stats.missing_glyphs > 0 && !s_missing_glyphs_logged) {
        s_missing_glyphs_logged = true;
        ESP_LOGW(TAG, MARKET_PANEL_MISSING_GLYPHS_FORMAT, stats.missing_glyphs);
    }
}

MarketPanelPause current_pause()
{
    if (battery_low_mode_load()) {
        return MarketPanelPause::kLowBattery;
    }
    if (offline_mode_enabled_load()) {
        return MarketPanelPause::kOffline;
    }
    return MarketPanelPause::kNone;
}

bool update_live_view(const MarketPanelStatus &live, uint32_t live_age)
{
    const MarketPanelPause pause = current_pause();
    const uint32_t status_key = market_panel_status_key(live_age, pause);
    if (s_drawn_view == PanelView::kLive && s_drawn_live_version == live.version &&
        s_drawn_status_key == status_key) {
        return false;
    }
    uint32_t version = 0;
    if (!market_panel_rows_copy(s_panel_frame.rows, &version)) {
        return false;
    }
    format_market_panel_status(live_age, pause, s_panel_frame.status,
                               sizeof(s_panel_frame.status));
    MarketPanelRenderStats stats;
    if (!render_market_panel(s_panel_frame, panel_fonts(), s_panel_bitmap,
                             sizeof(s_panel_bitmap), &stats)) {
        return false;
    }
    note_missing_glyphs(stats);
    blit_panel_bitmap();
    show_placeholder(false);
    s_drawn_view = PanelView::kLive;
    s_drawn_live_version = version;
    s_drawn_status_key = status_key;
    return true;
}

bool update_bitmap_view(const AibriefSnapshot &bitmap)
{
    // 右下角写最后一次成功读取位图的本地时间；失败重试不改变它。
    struct tm local = {};
    int read_minute = -1;
    if (bitmap.last_success > 0 && localtime_r(&bitmap.last_success, &local)) {
        read_minute = local.tm_hour * 60 + local.tm_min;
    }
    if (s_drawn_view == PanelView::kBitmap && s_drawn_bitmap_version == bitmap.version &&
        s_drawn_read_minute == read_minute) {
        return false;
    }
    if (!aibrief_bitmap_copy_current(s_panel_bitmap, sizeof(s_panel_bitmap))) {
        return false;
    }
    if (read_minute >= 0) {
        char status[kMarketPanelStatusBytes] = {};
        format_aibrief_read_indicator(status, sizeof(status), &local);
        MarketPanelRenderStats stats;
        if (draw_market_panel_status(panel_fonts(), status, s_panel_bitmap,
                                     sizeof(s_panel_bitmap), &stats)) {
            note_missing_glyphs(stats);
        }
    }
    blit_panel_bitmap();
    show_placeholder(false);
    s_drawn_view = PanelView::kBitmap;
    s_drawn_bitmap_version = bitmap.version;
    s_drawn_read_minute = read_minute;
    return true;
}

bool update_placeholder_view()
{
    if (s_drawn_view == PanelView::kPlaceholder) {
        return false;
    }
    lv_canvas_fill_bg(s_panel_canvas, lv_color_white(), LV_OPA_COVER);
    show_placeholder(true);
    s_drawn_view = PanelView::kPlaceholder;
    return true;
}
} // namespace

bool build_aibrief_page()
{
    if (auxiliary_page_root(AuxiliaryPage::kAibrief)) {
        return true;
    }
    lv_obj_t *root = create_page_root();
    if (!root) {
        return false;
    }
    set_auxiliary_page_root(AuxiliaryPage::kAibrief, root);
    lv_obj_add_flag(root, LV_OBJ_FLAG_HIDDEN);
    clear_aibrief_page_refs();
    lv_obj_t *canvas = lv_canvas_create(root);
    if (!canvas) {
        ESP_LOGW(TAG, "%s", MARKET_PANEL_CANVAS_CREATE_FAILED_LOG);
        return false;
    }
    if (!ensure_canvas_buffer(&s_panel_canvas_buffer, aibrief::kWidth, aibrief::kHeight)) {
        ESP_LOGW(TAG, "%s", MARKET_PANEL_BUFFER_ALLOC_FAILED_LOG);
        return false;
    }
    lv_canvas_set_buffer(canvas, s_panel_canvas_buffer, aibrief::kWidth, aibrief::kHeight,
                         LV_IMG_CF_TRUE_COLOR);
    lv_obj_set_pos(canvas, kPanelX, kPanelY);
    lv_canvas_fill_bg(canvas, lv_color_white(), LV_OPA_COVER);
    // canvas 和缓冲都就绪后才发布指针：update_aibrief_page 以它为「页面可画」的标志。
    s_panel_canvas = canvas;
    s_panel_empty_label = make_centered_label_with_font(root,
                                                        kEmptyLabelX,
                                                        kEmptyLabelY,
                                                        kEmptyLabelW,
                                                        kEmptyLabelH,
                                                        market_panel_layout::kPlaceholderText,
                                                        &market_panel_text_16,
                                                        MARKET_PANEL_LABEL_CREATE_FAILED_LOG);
    s_panel_hint_label = make_centered_label_with_font(root,
                                                       kHintLabelX,
                                                       kHintLabelY,
                                                       kHintLabelW,
                                                       kHintLabelH,
                                                       market_panel_layout::kPlaceholderHint,
                                                       &market_panel_text_16,
                                                       MARKET_PANEL_LABEL_CREATE_FAILED_LOG);
    show_placeholder(false);
    return true;
}

bool update_aibrief_page()
{
    if (!s_panel_canvas || !s_panel_canvas_buffer) {
        return false;
    }
    const MarketPanelStatus live = market_panel_status_load();
    const AibriefSnapshot bitmap = aibrief_snapshot_load();
    const uint32_t live_age =
        market_panel::age_seconds(live.refreshed_uptime, market_panel_uptime_seconds());
    const bool bitmap_available = bitmap.available && bitmap.page_count > 0;
    const bool bitmap_read_later =
        bitmap_available && bitmap.last_success > live.refreshed_wall;
    if (market_panel::prefer_live_view(live.available, live_age, bitmap_available,
                                       bitmap_read_later)) {
        return update_live_view(live, live_age);
    }
    if (bitmap_available) {
        return update_bitmap_view(bitmap);
    }
    return update_placeholder_view();
}

void clear_aibrief_page_refs()
{
    s_panel_canvas = nullptr;
    s_panel_canvas_buffer = nullptr;
    s_panel_empty_label = nullptr;
    s_panel_hint_label = nullptr;
    s_drawn_view = PanelView::kNone;
    s_drawn_live_version = 0;
    s_drawn_status_key = 0;
    s_drawn_bitmap_version = 0;
    s_drawn_read_minute = -1;
}
