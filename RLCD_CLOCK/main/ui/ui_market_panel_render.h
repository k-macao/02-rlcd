// 把行情数据面板画成 400x300 的 1bpp 位图：设备每分钟直连行情接口后在本机自绘。
//
// 输出与服务器位图（aibrief/latest.bin 里的一页）同一格式、同一版面、同一套字形，
// 所以界面层可以用同一段代码把「自绘面板」和「服务器位图」贴到屏幕上。
// 本文件只依赖 LVGL 的字库接口，不依赖显示驱动，主机测试会把它的输出与
// device_brief/render_brief.py 的位图逐像素比对。
#pragma once

#include "market_panel_contract.h"

#include "lvgl.h"

#include <stddef.h>
#include <stdint.h>

inline constexpr size_t kMarketPanelStatusBytes = 48;
inline constexpr size_t kMarketPanelBitmapBytes = 400U / 8U * 300U;

struct MarketPanelFrame {
    market_panel::Quote rows[market_panel::kRowCount];
    // 页脚右侧的状态文字（UTF-8，可为空）；数值串自动用 Bold。
    char status[kMarketPanelStatusBytes] = {};
};

struct MarketPanelFonts {
    const lv_font_t *regular = nullptr;
    const lv_font_t *bold = nullptr;
};

struct MarketPanelRenderStats {
    // 字库里找不到的字符数；非零说明界面文字超出了字库清单。
    int missing_glyphs = 0;
};

// bitmap 为 400x300 的 1bpp 位图（MSB first，1 = 黑），长度必须是 15000 字节。
bool render_market_panel(const MarketPanelFrame &frame,
                         const MarketPanelFonts &fonts,
                         uint8_t *bitmap,
                         size_t bitmap_len,
                         MarketPanelRenderStats *stats = nullptr);

// 在一张已有的面板位图上重写页脚右侧的状态文字：先把状态区清成白底，再右对齐写入
// status（可为空，只清空）。显示服务器渲染的位图时用它写上「读取 HH:MM」。
bool draw_market_panel_status(const MarketPanelFonts &fonts,
                              const char *status,
                              uint8_t *bitmap,
                              size_t bitmap_len,
                              MarketPanelRenderStats *stats = nullptr);

// 与渲染器的数值串规则 [+-]?\d+(?:[.,:/-]\d+)*%? 相同：返回从 text 起始处开始的
// 数值串长度（字节），不是数值串时返回 0。数值串用 Bold，其余文字用 Regular。
size_t market_panel_numeric_run_length(const char *text);

// 一段文字按面板的字重规则排版后的像素宽度；missing 累加字库里缺的字符数。
int market_panel_text_width(const MarketPanelFonts &fonts,
                            const char *text,
                            bool bold,
                            int *missing = nullptr);
