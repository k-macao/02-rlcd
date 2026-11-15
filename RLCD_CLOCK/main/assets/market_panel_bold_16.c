// 行情数据面板的数值与标题字库：阿里巴巴普惠体 3.0 Bold 16px 单色子集。由 RLCD_CLOCK/tools/gen_market_panel_fonts.py 生成，请勿手改。
/*******************************************************************************
 * Size: 16 px
 * Bpp: 1
 * Opts: --font device_brief/fonts/AlibabaPuHuiTi-3-85-Bold.ttf -r 0x20,0x25,0x2B,0x2C,0x2D,0x2E,0x2F,0x30,0x31,0x32,0x33,0x34,0x35,0x36,0x37,0x38,0x39,0x3A,0x41,0x5168,0x60C5,0x65E5,0x6E2F,0x7403,0x7F8E,0x884C,0x97E9 --size 16 --bpp 1 --format lvgl --no-kerning --no-compress --lv-include lvgl.h --lv-font-name market_panel_bold_16 --output RLCD_CLOCK/main/assets/market_panel_bold_16.c
 ******************************************************************************/

#ifdef LV_LVGL_H_INCLUDE_SIMPLE
#include "lvgl.h"
#else
#include "lvgl.h"
#endif

#ifndef MARKET_PANEL_BOLD_16
#define MARKET_PANEL_BOLD_16 1
#endif

#if MARKET_PANEL_BOLD_16

/*-----------------
 *    BITMAPS
 *----------------*/

/*Store the image of the glyphs*/
static LV_ATTRIBUTE_LARGE_CONST const uint8_t glyph_bitmap[] = {
    /* U+0020 " " */
    0x0,

    /* U+0025 "%" */
    0x70, 0x8d, 0x98, 0xd9, 0xd, 0xa0, 0xda, 0x7,
    0x5e, 0x7, 0x30, 0xb3, 0x13, 0x31, 0x33, 0x21,
    0xe0,

    /* U+002B "+" */
    0x18, 0x18, 0x18, 0xff, 0x18, 0x18, 0x18, 0x18,

    /* U+002C "," */
    0x6f, 0x60,

    /* U+002D "-" */
    0xf8,

    /* U+002E "." */
    0xfc,

    /* U+002F "/" */
    0xc, 0x30, 0x86, 0x18, 0x43, 0xc, 0x21, 0x86,
    0x10, 0xc0,

    /* U+0030 "0" */
    0x3c, 0x66, 0xc3, 0xc3, 0xc3, 0xc3, 0xc3, 0xc3,
    0xc3, 0x66, 0x3c,

    /* U+0031 "1" */
    0x7f, 0xc6, 0x31, 0x8c, 0x63, 0x18, 0xc6,

    /* U+0032 "2" */
    0x7c, 0x6, 0x6, 0x6, 0x6, 0xe, 0x1c, 0x38,
    0x70, 0xe0, 0xff,

    /* U+0033 "3" */
    0xfc, 0xc, 0x18, 0x77, 0x81, 0xc1, 0x83, 0x6,
    0x1b, 0xe0,

    /* U+0034 "4" */
    0xe, 0xf, 0x7, 0x86, 0xc6, 0x63, 0x33, 0x19,
    0xff, 0x6, 0x3, 0x1, 0x80,

    /* U+0035 "5" */
    0x7c, 0x83, 0x6, 0xf, 0xc1, 0xc1, 0x83, 0x6,
    0x1b, 0xe0,

    /* U+0036 "6" */
    0x1e, 0x30, 0x60, 0x40, 0xfe, 0xe7, 0xc3, 0xc3,
    0xc3, 0x66, 0x3c,

    /* U+0037 "7" */
    0xfe, 0xc, 0x38, 0x60, 0xc3, 0x6, 0x1c, 0x30,
    0x61, 0x80,

    /* U+0038 "8" */
    0x7e, 0xc3, 0xc3, 0xc3, 0x3c, 0x66, 0xc3, 0xc3,
    0xc3, 0xe7, 0x3c,

    /* U+0039 "9" */
    0x3c, 0x66, 0xc3, 0xc3, 0xc3, 0xe7, 0x7b, 0x7,
    0x6, 0xc, 0x38,

    /* U+003A ":" */
    0xfc, 0xf, 0xc0,

    /* U+0041 "A" */
    0xe, 0x1, 0xe0, 0x6c, 0xd, 0xc1, 0x98, 0x63,
    0xc, 0x73, 0xfe, 0x60, 0xcc, 0x1f, 0x81, 0x80,

    /* U+5168 "全" */
    0x3, 0x80, 0x7, 0x0, 0x1b, 0x0, 0x77, 0x1,
    0xc7, 0xf, 0x7, 0xbf, 0xff, 0x81, 0x80, 0x3,
    0x0, 0x7f, 0xf0, 0xc, 0x0, 0x18, 0x0, 0x30,
    0xf, 0xff, 0x80,

    /* U+60C5 "情" */
    0x30, 0x60, 0x6f, 0xf8, 0xe1, 0x87, 0xff, 0xef,
    0x6, 0x16, 0xff, 0xec, 0x0, 0x59, 0xfe, 0x32,
    0xc, 0x67, 0xf8, 0xc8, 0x31, 0x9f, 0xe3, 0x20,
    0xc6, 0x47, 0x8c, 0x86, 0x0,

    /* U+65E5 "日" */
    0xff, 0xff, 0xff, 0x1, 0xe0, 0x3c, 0x7, 0xff,
    0xff, 0xfe, 0x3, 0xc0, 0x78, 0xf, 0x1, 0xff,
    0xff, 0xfe,

    /* U+6E2F "港" */
    0x6, 0x63, 0x99, 0x87, 0xff, 0xc9, 0x98, 0x86,
    0x63, 0x7f, 0xf6, 0x63, 0x13, 0xe, 0x1f, 0xfd,
    0x70, 0xd6, 0xff, 0x33, 0x2, 0xcc, 0x1b, 0x1f,
    0xe0,

    /* U+7403 "球" */
    0x0, 0xd0, 0x1, 0xb3, 0xc3, 0x23, 0x7f, 0xe6,
    0xc, 0xc, 0x99, 0x1b, 0xbb, 0x7b, 0x7c, 0x63,
    0xf0, 0xc7, 0xe1, 0xbb, 0x63, 0xa6, 0x6f, 0xc,
    0x50, 0x78, 0x0, 0xe0, 0x0,

    /* U+7F8E "美" */
    0x0, 0x0, 0xc6, 0x2, 0x30, 0xff, 0xe0, 0x20,
    0x3f, 0xf8, 0x8, 0x1f, 0xff, 0xff, 0xf8, 0x10,
    0x1f, 0xfc, 0xe, 0x1, 0xde, 0x7c, 0x7f, 0x0,
    0x60,

    /* U+884C "行" */
    0x0, 0x0, 0x6f, 0xe7, 0x0, 0x38, 0x0, 0xc8,
    0x0, 0x60, 0x3, 0x7f, 0xdc, 0xc, 0xf0, 0x32,
    0xc0, 0xc3, 0x3, 0xc, 0xc, 0x31, 0xe0, 0xc3,
    0x80,

    /* U+97E9 "韩" */
    0x10, 0x60, 0x41, 0x8f, 0xdf, 0xc4, 0x18, 0x7c,
    0x61, 0x17, 0xf7, 0xc6, 0x11, 0x18, 0x47, 0xfd,
    0xff, 0xf1, 0x6, 0x7f, 0x9b, 0x10, 0x6c, 0x41,
    0x81, 0x6, 0x0
};


/*---------------------
 *  GLYPH DESCRIPTION
 *--------------------*/

static const lv_font_fmt_txt_glyph_dsc_t glyph_dsc[] = {
    {.bitmap_index = 0, .adv_w = 0, .box_w = 0, .box_h = 0, .ofs_x = 0, .ofs_y = 0} /* id = 0 reserved */,
    {.bitmap_index = 0, .adv_w = 66, .box_w = 1, .box_h = 1, .ofs_x = 0, .ofs_y = 0},
    {.bitmap_index = 1, .adv_w = 212, .box_w = 12, .box_h = 11, .ofs_x = 1, .ofs_y = 0},
    {.bitmap_index = 18, .adv_w = 147, .box_w = 8, .box_h = 8, .ofs_x = 1, .ofs_y = 1},
    {.bitmap_index = 26, .adv_w = 83, .box_w = 3, .box_h = 4, .ofs_x = 1, .ofs_y = -2},
    {.bitmap_index = 28, .adv_w = 113, .box_w = 5, .box_h = 1, .ofs_x = 1, .ofs_y = 5},
    {.bitmap_index = 29, .adv_w = 83, .box_w = 2, .box_h = 3, .ofs_x = 1, .ofs_y = 0},
    {.bitmap_index = 30, .adv_w = 128, .box_w = 6, .box_h = 13, .ofs_x = 1, .ofs_y = 0},
    {.bitmap_index = 40, .adv_w = 150, .box_w = 8, .box_h = 11, .ofs_x = 1, .ofs_y = 0},
    {.bitmap_index = 51, .adv_w = 150, .box_w = 5, .box_h = 11, .ofs_x = 1, .ofs_y = 0},
    {.bitmap_index = 58, .adv_w = 150, .box_w = 8, .box_h = 11, .ofs_x = 1, .ofs_y = 0},
    {.bitmap_index = 69, .adv_w = 150, .box_w = 7, .box_h = 11, .ofs_x = 1, .ofs_y = 0},
    {.bitmap_index = 79, .adv_w = 150, .box_w = 9, .box_h = 11, .ofs_x = 1, .ofs_y = 0},
    {.bitmap_index = 92, .adv_w = 150, .box_w = 7, .box_h = 11, .ofs_x = 1, .ofs_y = 0},
    {.bitmap_index = 102, .adv_w = 150, .box_w = 8, .box_h = 11, .ofs_x = 1, .ofs_y = 0},
    {.bitmap_index = 113, .adv_w = 150, .box_w = 7, .box_h = 11, .ofs_x = 1, .ofs_y = 0},
    {.bitmap_index = 123, .adv_w = 150, .box_w = 8, .box_h = 11, .ofs_x = 1, .ofs_y = 0},
    {.bitmap_index = 134, .adv_w = 150, .box_w = 8, .box_h = 11, .ofs_x = 1, .ofs_y = 0},
    {.bitmap_index = 145, .adv_w = 102, .box_w = 2, .box_h = 9, .ofs_x = 2, .ofs_y = 0},
    {.bitmap_index = 148, .adv_w = 183, .box_w = 11, .box_h = 11, .ofs_x = 0, .ofs_y = 0},
    {.bitmap_index = 164, .adv_w = 252, .box_w = 15, .box_h = 14, .ofs_x = 0, .ofs_y = -1},
    {.bitmap_index = 191, .adv_w = 252, .box_w = 15, .box_h = 15, .ofs_x = 0, .ofs_y = -2},
    {.bitmap_index = 220, .adv_w = 252, .box_w = 11, .box_h = 13, .ofs_x = 3, .ofs_y = -1},
    {.bitmap_index = 238, .adv_w = 252, .box_w = 14, .box_h = 14, .ofs_x = 1, .ofs_y = -1},
    {.bitmap_index = 263, .adv_w = 252, .box_w = 15, .box_h = 15, .ofs_x = 1, .ofs_y = -2},
    {.bitmap_index = 292, .adv_w = 252, .box_w = 13, .box_h = 15, .ofs_x = 1, .ofs_y = -1},
    {.bitmap_index = 317, .adv_w = 252, .box_w = 14, .box_h = 14, .ofs_x = 1, .ofs_y = -1},
    {.bitmap_index = 342, .adv_w = 252, .box_w = 14, .box_h = 15, .ofs_x = 1, .ofs_y = -2}
};

/*---------------------
 *  CHARACTER MAPPING
 *--------------------*/

static const uint16_t unicode_list_0[] = {
    0x0, 0x5, 0xb, 0xc, 0xd, 0xe, 0xf, 0x10,
    0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18,
    0x19, 0x1a, 0x21, 0x5148, 0x60a5, 0x65c5, 0x6e0f, 0x73e3,
    0x7f6e, 0x882c, 0x97c9
};

/*Collect the unicode lists and glyph_id offsets*/
static const lv_font_fmt_txt_cmap_t cmaps[] =
{
    {
        .range_start = 32, .range_length = 38858, .glyph_id_start = 1,
        .unicode_list = unicode_list_0, .glyph_id_ofs_list = NULL, .list_length = 27, .type = LV_FONT_FMT_TXT_CMAP_SPARSE_TINY
    }
};



/*--------------------
 *  ALL CUSTOM DATA
 *--------------------*/

#if LVGL_VERSION_MAJOR == 8
/*Store all the custom data of the font*/
static  lv_font_fmt_txt_glyph_cache_t cache;
#endif

#if LVGL_VERSION_MAJOR >= 8
static const lv_font_fmt_txt_dsc_t font_dsc = {
#else
static lv_font_fmt_txt_dsc_t font_dsc = {
#endif
    .glyph_bitmap = glyph_bitmap,
    .glyph_dsc = glyph_dsc,
    .cmaps = cmaps,
    .kern_dsc = NULL,
    .kern_scale = 0,
    .cmap_num = 1,
    .bpp = 1,
    .kern_classes = 0,
    .bitmap_format = 0,
#if LVGL_VERSION_MAJOR == 8
    .cache = &cache
#endif
};



/*-----------------
 *  PUBLIC FONT
 *----------------*/

/*Initialize a public general font descriptor*/
#if LVGL_VERSION_MAJOR >= 8
const lv_font_t market_panel_bold_16 = {
#else
lv_font_t market_panel_bold_16 = {
#endif
    .get_glyph_dsc = lv_font_get_glyph_dsc_fmt_txt,    /*Function pointer to get glyph's data*/
    .get_glyph_bitmap = lv_font_get_bitmap_fmt_txt,    /*Function pointer to get glyph's bitmap*/
    .line_height = 16,          /*The maximum line height required by the font*/
    .base_line = 2,             /*Baseline measured from the bottom of the line*/
#if !(LVGL_VERSION_MAJOR == 6 && LVGL_VERSION_MINOR == 0)
    .subpx = LV_FONT_SUBPX_NONE,
#endif
#if LV_VERSION_CHECK(7, 4, 0) || LVGL_VERSION_MAJOR >= 8
    .underline_position = -1,
    .underline_thickness = 1,
#endif
    .dsc = &font_dsc,          /*The custom font data. Will be accessed by `get_glyph_bitmap/dsc` */
#if LV_VERSION_CHECK(8, 2, 0) || LVGL_VERSION_MAJOR >= 9
    .fallback = NULL,
#endif
    .user_data = NULL,
};



#endif /*#if MARKET_PANEL_BOLD_16*/

