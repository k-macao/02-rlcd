// 构建章鱼 AI 简报辅助页：把 1bpp 位图解码到全屏 LVGL canvas。
#include "ui_aibrief_page.h"

#include "aibrief_contract.h"
#include "aibrief_state.h"
#include "app_constexpr.h"
#include "app_metadata.h"
#include "ui_canvas_primitives.h"
#include "ui_fonts.h"
#include "ui_page_state.h"
#include "ui_widgets.h"

#include <esp_attr.h>
#include <esp_log.h>

#include <stdint.h>

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
constexpr const char *kAibriefEmptyText = "章鱼AI 简报尚未获取";
constexpr const char *kAibriefHintText = "长按 KEY 返回";

lv_obj_t *s_aibrief_canvas;
lv_color_t *s_aibrief_canvas_buffer;
lv_obj_t *s_aibrief_empty_label;
lv_obj_t *s_aibrief_hint_label;
uint32_t s_aibrief_drawn_version;
EXT_RAM_BSS_ATTR uint8_t s_aibrief_bitmap[aibrief::kBitmapBytes];

static_assert(aibrief::kWidth == 400 && aibrief::kHeight == 300,
              "aibrief page expects the 400x300 RLCD panel");
static_assert(kAibriefEmptyLabelW > 0 && kAibriefHintLabelH > 0,
              "aibrief placeholder labels must have positive size");

void show_placeholder(bool visible)
{
    set_obj_visible(s_aibrief_empty_label, visible);
    set_obj_visible(s_aibrief_hint_label, visible);
}

bool draw_bitmap_to_canvas()
{
    if (!s_aibrief_canvas) {
        return false;
    }
    if (!aibrief_bitmap_copy(s_aibrief_bitmap, sizeof(s_aibrief_bitmap))) {
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
                                                          &zh_font_16,
                                                          AIBRIEF_LABEL_CREATE_FAILED_LOG);
    s_aibrief_hint_label = make_centered_label_with_font(root,
                                                         kAibriefHintLabelX,
                                                         kAibriefHintLabelY,
                                                         kAibriefHintLabelW,
                                                         kAibriefHintLabelH,
                                                         kAibriefHintText,
                                                         &zh_font_16,
                                                         AIBRIEF_LABEL_CREATE_FAILED_LOG);
    s_aibrief_drawn_version = UINT32_MAX;
    return true;
}

bool update_aibrief_page()
{
    const AibriefSnapshot snapshot = aibrief_snapshot_load();
    const uint32_t drawn_version = snapshot.available ? snapshot.version : 0;
    if (drawn_version == s_aibrief_drawn_version) {
        return false;
    }
    s_aibrief_drawn_version = drawn_version;
    if (snapshot.available) {
        if (!draw_bitmap_to_canvas()) {
            return false;
        }
        show_placeholder(false);
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
    s_aibrief_drawn_version = UINT32_MAX;
}
