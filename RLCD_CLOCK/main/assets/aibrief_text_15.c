/*******************************************************************************
 * Size: 15 px
 * Bpp: 1
 * Opts: --font /tmp/aibrief-fonts/text-regular.ttf -r 0x20,0x2F,0xB7,0x53D6,0x8BFB --size 15 --bpp 1 --format lvgl --no-kerning --lv-include lvgl.h --lv-font-name aibrief_text_15 --output RLCD_CLOCK/main/assets/aibrief_text_15.c
 ******************************************************************************/

#ifdef LV_LVGL_H_INCLUDE_SIMPLE
#include "lvgl.h"
#else
#include "lvgl.h"
#endif

#ifndef AIBRIEF_TEXT_15
#define AIBRIEF_TEXT_15 1
#endif

#if AIBRIEF_TEXT_15

/*-----------------
 *    BITMAPS
 *----------------*/

/*Store the image of the glyphs*/
static LV_ATTRIBUTE_LARGE_CONST const uint8_t glyph_bitmap[] = {
    /* U+0020 " " */
    0x0,

    /* U+002F "/" */
    0xc, 0x20, 0x82, 0x10, 0x41, 0x8, 0x20, 0x84,
    0x10, 0x42, 0x0,

    /* U+00B7 "·" */
    0xff, 0x80,

    /* U+53D6 "取" */
    0xfe, 0x1, 0x97, 0xf6, 0x48, 0xdf, 0x22, 0x64,
    0x59, 0x91, 0x67, 0xc5, 0x99, 0xc, 0x64, 0x31,
    0xfd, 0xce, 0x45, 0x1, 0x33, 0x5, 0x84,

    /* U+8BFB "读" */
    0x40, 0xc1, 0x83, 0x3, 0x7f, 0x84, 0x30, 0xf,
    0xff, 0x81, 0x22, 0x34, 0x89, 0x10, 0x22, 0x40,
    0x9f, 0xf2, 0x88, 0xc, 0x6c, 0x23, 0x8, 0x10,
    0x10
};


/*---------------------
 *  GLYPH DESCRIPTION
 *--------------------*/

static const lv_font_fmt_txt_glyph_dsc_t glyph_dsc[] = {
    {.bitmap_index = 0, .adv_w = 0, .box_w = 0, .box_h = 0, .ofs_x = 0, .ofs_y = 0} /* id = 0 reserved */,
    {.bitmap_index = 0, .adv_w = 54, .box_w = 1, .box_h = 1, .ofs_x = 0, .ofs_y = 0},
    {.bitmap_index = 1, .adv_w = 94, .box_w = 6, .box_h = 14, .ofs_x = 0, .ofs_y = -3},
    {.bitmap_index = 12, .adv_w = 240, .box_w = 3, .box_h = 3, .ofs_x = 6, .ofs_y = 4},
    {.bitmap_index = 14, .adv_w = 240, .box_w = 14, .box_h = 13, .ofs_x = 0, .ofs_y = -1},
    {.bitmap_index = 37, .adv_w = 240, .box_w = 14, .box_h = 14, .ofs_x = 1, .ofs_y = -1}
};

/*---------------------
 *  CHARACTER MAPPING
 *--------------------*/

static const uint16_t unicode_list_0[] = {
    0x0, 0xf, 0x97, 0x53b6, 0x8bdb
};

/*Collect the unicode lists and glyph_id offsets*/
static const lv_font_fmt_txt_cmap_t cmaps[] =
{
    {
        .range_start = 32, .range_length = 35804, .glyph_id_start = 1,
        .unicode_list = unicode_list_0, .glyph_id_ofs_list = NULL, .list_length = 5, .type = LV_FONT_FMT_TXT_CMAP_SPARSE_TINY
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
const lv_font_t aibrief_text_15 = {
#else
lv_font_t aibrief_text_15 = {
#endif
    .get_glyph_dsc = lv_font_get_glyph_dsc_fmt_txt,    /*Function pointer to get glyph's data*/
    .get_glyph_bitmap = lv_font_get_bitmap_fmt_txt,    /*Function pointer to get glyph's bitmap*/
    .line_height = 16,          /*The maximum line height required by the font*/
    .base_line = 3,             /*Baseline measured from the bottom of the line*/
#if !(LVGL_VERSION_MAJOR == 6 && LVGL_VERSION_MINOR == 0)
    .subpx = LV_FONT_SUBPX_NONE,
#endif
#if LV_VERSION_CHECK(7, 4, 0) || LVGL_VERSION_MAJOR >= 8
    .underline_position = -2,
    .underline_thickness = 1,
#endif
    .dsc = &font_dsc,          /*The custom font data. Will be accessed by `get_glyph_bitmap/dsc` */
#if LV_VERSION_CHECK(8, 2, 0) || LVGL_VERSION_MAJOR >= 9
    .fallback = NULL,
#endif
    .user_data = NULL,
};



#endif /*#if AIBRIEF_TEXT_15*/

