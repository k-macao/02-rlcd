// 构建章鱼 AI 简报辅助页：把多页 1bpp 位图解码到全屏 LVGL canvas，每分钟自动翻页。
#include "ui_aibrief_page.h"

#include "aibrief_contract.h"
#include "aibrief_state.h"
#include "app_constexpr.h"
#include "app_metadata.h"
#include "ui_aibrief_page_format.h"
#include "ui_canvas_primitives.h"
#include "ui_fonts.h"
#include "ui_page_state.h"
#include "ui_widgets.h"

#include <esp_attr.h>
#include <esp_log.h>
#include <esp_timer.h>

#include <cstdint>
#include <cstring>

namespace {
#define AIBRIEF_CANVAS_CREATE_FAILED_LOG "aibrief canvas create failed"
#define AIBRIEF_BUFFER_ALLOC_FAILED_LOG "aibrief canvas buffer alloc failed"
#define AIBRIEF_LABEL_CREATE_FAILED_LOG "aibrief label create failed"

constexpr int kAibriefPageX = 0;
constexpr int kAibriefPageY = 0;
constexpr int kAibriefEmptyLabelX = 40;
constexpr int kAibriefEmptyLabelY = 122;
constexpr int kAibriefEmptyLabelW = 320;
constexpr int kAibriefEmptyLabelH = 24;
constexpr int kAibriefHintLabelX = 40;
constexpr int kAibriefHintLabelY = 152;
constexpr int kAibriefHintLabelW = 320;
constexpr int kAibriefHintLabelH = 20;
constexpr int kAibriefPageIndicatorX = 194;
constexpr int kAibriefPageIndicatorY = 279;
constexpr int kAibriefPageIndicatorW = 194;
constexpr int kAibriefPageIndicatorH = 21;
// 位图页脚从 y=279 起绘制；阿里巴巴普惠体 16px 的 Pillow ascender 为 17px。
constexpr int kAibriefBitmapFooterBaseline = 17;
// 页脚只显示「读取 HH:MM」：前缀 + 时间两段，页码已取消。
constexpr int kAibriefPageSegmentCount = 2;
constexpr const char *kAibriefEmptyText = "章鱼AI 简报尚未获取";
constexpr const char *kAibriefHintText = "长按 KEY 返回 · 自动每分钟翻页";

lv_obj_t *s_aibrief_canvas;
lv_color_t *s_aibrief_canvas_buffer;
lv_obj_t *s_aibrief_empty_label;
lv_obj_t *s_aibrief_hint_label;
lv_obj_t *s_aibrief_page_indicator;
lv_obj_t *s_aibrief_page_segment_labels[kAibriefPageSegmentCount];
uint32_t s_aibrief_drawn_version;
uint8_t s_aibrief_drawn_page;
int64_t s_aibrief_last_page_switch_us;
EXT_RAM_BSS_ATTR uint8_t s_aibrief_bitmap[aibrief::kBitmapBytes];

static_assert(aibrief::kWidth == 400 && aibrief::kHeight == 300,
              "aibrief page expects the 400x300 RLCD panel");
static_assert(kAibriefEmptyLabelW > 0 && kAibriefHintLabelH > 0,
              "aibrief placeholder labels must have positive size");

void show_placeholder(bool visible)
{
    set_obj_visible(s_aibrief_empty_label, visible);
    set_obj_visible(s_aibrief_hint_label, visible);
    if (s_aibrief_page_indicator) {
        set_obj_visible(s_aibrief_page_indicator, !visible);
    }
}

bool draw_bitmap_to_canvas()
{
    if (!s_aibrief_canvas) {
        return false;
    }
    if (!aibrief_bitmap_copy_current(s_aibrief_bitmap, sizeof(s_aibrief_bitmap))) {
        return false;
    }
    lv_img_dsc_t *image = lv_canvas_get_img(s_aibrief_canvas);
    if (!image) {
        return false;
    }
    for (int y = 0; y < aibrief::kHeight; ++y) {
        const uint8_t *row = s_aibrief_bitmap + y * aibrief::kBytesPerRow;
        for (int x = 0; x < aibrief::kWidth; ++x) {
            const bool black =
                aibrief::bit_is_set(row, static_cast<uint32_t>(x));
            lv_img_buf_set_px_color(image,
                                    x,
                                    y,
                                    black ? lv_color_black()
                                          : lv_color_white());
        }
    }
    lv_obj_invalidate(s_aibrief_canvas);
    return true;
}

bool page_indicator_ready()
{
    if (!s_aibrief_page_indicator) {
        return false;
    }
    for (const lv_obj_t *label : s_aibrief_page_segment_labels) {
        if (!label) {
            return false;
        }
    }
    return true;
}

void update_page_indicator()
{
    if (!page_indicator_ready()) {
        // 字体标签不完整时保留位图内页脚，不能用空白覆盖层遮住它。
        if (s_aibrief_page_indicator) {
            set_obj_visible(s_aibrief_page_indicator, false);
        }
        return;
    }
    const AibriefSnapshot snap = aibrief_snapshot_load();
    if (!snap.available || snap.page_count == 0) {
        set_obj_visible(s_aibrief_page_indicator, false);
        return;
    }

    // 单页也显示最后一次成功读取的本地时间；失败重试不改变该时间。
    struct tm local = {};
    const struct tm *read_time = nullptr;
    if (snap.last_success > 0 && localtime_r(&snap.last_success, &local)) {
        read_time = &local;
    }
    if (!read_time) {
        // 还没有成功读取时间：不覆盖位图内页脚，避免留下空白遮挡。
        set_obj_visible(s_aibrief_page_indicator, false);
        return;
    }
    set_obj_visible(s_aibrief_page_indicator, true);

    AibriefReadIndicatorParts parts;
    format_aibrief_read_indicator_parts(read_time, parts);
    const char *texts[kAibriefPageSegmentCount] = {
        parts.prefix, parts.read_time,
    };
    const lv_font_t *fonts[kAibriefPageSegmentCount] = {
        &aibrief_text_16, &aibrief_digit_bold_16,
    };
    int widths[kAibriefPageSegmentCount] = {};
    int total_width = 0;
    for (int i = 0; i < kAibriefPageSegmentCount; ++i) {
        lv_label_set_text(s_aibrief_page_segment_labels[i], texts[i]);
        if (texts[i][0]) {
            widths[i] = lv_txt_get_width(
                texts[i], static_cast<uint32_t>(std::strlen(texts[i])),
                fonts[i], 0, LV_TEXT_FLAG_NONE);
            total_width += widths[i];
        }
    }
    const int text_baseline = aibrief_text_16.line_height - aibrief_text_16.base_line;
    const int digit_baseline =
        aibrief_digit_bold_16.line_height - aibrief_digit_bold_16.base_line;
    const int text_y = kAibriefBitmapFooterBaseline - text_baseline;
    const int digit_y = kAibriefBitmapFooterBaseline - digit_baseline;
    int x = kAibriefPageIndicatorW - total_width;
    for (int i = 0; i < kAibriefPageSegmentCount; ++i) {
        // 中文和数字都与位图左侧页脚共用同一基线。
        lv_obj_set_pos(s_aibrief_page_segment_labels[i], x, i % 2 ? digit_y : text_y);
        lv_obj_set_size(s_aibrief_page_segment_labels[i],
                        widths[i], fonts[i]->line_height);
        x += widths[i];
    }
}

void clear_canvas()
{
    if (s_aibrief_canvas) {
        lv_canvas_fill_bg(s_aibrief_canvas, lv_color_white(), LV_OPA_COVER);
    }
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
    s_aibrief_canvas = lv_canvas_create(root);
    if (!s_aibrief_canvas) {
        ESP_LOGW(TAG, "%s", AIBRIEF_CANVAS_CREATE_FAILED_LOG);
        return false;
    }
    if (!ensure_canvas_buffer(&s_aibrief_canvas_buffer,
                              aibrief::kWidth,
                              aibrief::kHeight)) {
        ESP_LOGW(TAG, "%s", AIBRIEF_BUFFER_ALLOC_FAILED_LOG);
        return false;
    }
    lv_canvas_set_buffer(s_aibrief_canvas,
                         s_aibrief_canvas_buffer,
                         aibrief::kWidth,
                         aibrief::kHeight,
                         LV_IMG_CF_TRUE_COLOR);
    lv_obj_set_pos(s_aibrief_canvas, kAibriefPageX, kAibriefPageY);
    clear_canvas();
    s_aibrief_empty_label = make_centered_label_with_font(root,
                                                          kAibriefEmptyLabelX,
                                                          kAibriefEmptyLabelY,
                                                          kAibriefEmptyLabelW,
                                                          kAibriefEmptyLabelH,
                                                          kAibriefEmptyText,
                                                          &aibrief_text_16,
                                                          AIBRIEF_LABEL_CREATE_FAILED_LOG);
    s_aibrief_hint_label = make_centered_label_with_font(root,
                                                         kAibriefHintLabelX,
                                                         kAibriefHintLabelY,
                                                         kAibriefHintLabelW,
                                                         kAibriefHintLabelH,
                                                         kAibriefHintText,
                                                         &aibrief_text_16,
                                                         AIBRIEF_LABEL_CREATE_FAILED_LOG);
    // 右下不透明白底覆盖位图回退页脚；六段标签混排 Regular 与 Bold 16px。
    s_aibrief_page_indicator = lv_obj_create(root);
    if (s_aibrief_page_indicator) {
        lv_obj_set_pos(s_aibrief_page_indicator, kAibriefPageIndicatorX, kAibriefPageIndicatorY);
        lv_obj_set_size(s_aibrief_page_indicator, kAibriefPageIndicatorW, kAibriefPageIndicatorH);
        lv_obj_set_style_bg_color(s_aibrief_page_indicator, lv_color_white(), 0);
        lv_obj_set_style_bg_opa(s_aibrief_page_indicator, LV_OPA_COVER, 0);
        lv_obj_set_style_pad_all(s_aibrief_page_indicator, 0, 0);
        lv_obj_set_style_border_width(s_aibrief_page_indicator, 0, 0);
        lv_obj_set_style_radius(s_aibrief_page_indicator, 0, 0);
        lv_obj_clear_flag(s_aibrief_page_indicator, LV_OBJ_FLAG_SCROLLABLE);
        for (int i = 0; i < kAibriefPageSegmentCount; ++i) {
            lv_obj_t *label = lv_label_create(s_aibrief_page_indicator);
            s_aibrief_page_segment_labels[i] = label;
            if (!label) {
                continue;
            }
            const lv_font_t *font = i % 2 ? &aibrief_digit_bold_16 : &aibrief_text_16;
            lv_obj_set_style_text_font(label, font, 0);
            lv_obj_set_style_text_color(label, lv_color_black(), 0);
            lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_LEFT, 0);
            lv_obj_set_style_pad_all(label, 0, 0);
            lv_obj_set_style_bg_opa(label, LV_OPA_TRANSP, 0);
            lv_label_set_long_mode(label, LV_LABEL_LONG_CLIP);
            lv_label_set_text(label, "");
        }
        set_obj_visible(s_aibrief_page_indicator, false);
    }
    s_aibrief_drawn_version = UINT32_MAX;
    s_aibrief_drawn_page = 0xFF;
    s_aibrief_last_page_switch_us = esp_timer_get_time();
    return true;
}

bool update_aibrief_page()
{
    const AibriefSnapshot snapshot = aibrief_snapshot_load();
    const uint32_t drawn_version = snapshot.available ? snapshot.version : 0;
    const uint8_t current_page = snapshot.available ? snapshot.current_page : 0;

    // 检查是否需要自动翻页：每 60 秒切下一页
    if (snapshot.available && snapshot.page_count > 1) {
        const int64_t now_us = esp_timer_get_time();
        const int64_t elapsed_us = now_us - s_aibrief_last_page_switch_us;
        if (elapsed_us >= static_cast<int64_t>(aibrief::kPageRotateSeconds) * 1000000LL) {
            if (aibrief_advance_page()) {
                s_aibrief_last_page_switch_us = now_us;
                // 版本号已在 advance 中递增，下一轮会触发重绘
                return true;
            }
        }
    }

    // 版本或页码变化才重绘
    if (drawn_version == s_aibrief_drawn_version && current_page == s_aibrief_drawn_page) {
        return false;
    }
    s_aibrief_drawn_version = drawn_version;
    s_aibrief_drawn_page = current_page;

    if (snapshot.available) {
        if (!draw_bitmap_to_canvas()) {
            return false;
        }
        show_placeholder(false);
        update_page_indicator();
        s_aibrief_last_page_switch_us = esp_timer_get_time();
        return true;
    }
    clear_canvas();
    show_placeholder(true);
    return true;
}

void clear_aibrief_page_refs()
{
    s_aibrief_canvas = nullptr;
    s_aibrief_canvas_buffer = nullptr;
    s_aibrief_empty_label = nullptr;
    s_aibrief_hint_label = nullptr;
    s_aibrief_page_indicator = nullptr;
    for (lv_obj_t *&label : s_aibrief_page_segment_labels) {
        label = nullptr;
    }
    s_aibrief_drawn_version = UINT32_MAX;
    s_aibrief_drawn_page = 0xFF;
    s_aibrief_last_page_switch_us = 0;
}
