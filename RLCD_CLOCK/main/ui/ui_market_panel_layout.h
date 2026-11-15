// 定义行情数据面板在 400x300 屏上的版面坐标与固定文字。
//
// 服务器渲染器 device_brief/render_brief.py 用同一套坐标出位图（旧固件显示、
// 新固件兜底），device_brief/test_render_brief.py 会逐项比对两边的常量；
// 改这里必须同步改渲染器。所有文字 y 都是「文字原点」：阿里巴巴普惠体 16px 的
// 基线在原点下方 kTextBaseline 像素，汉字墨迹占原点下方第 kInkTop..kInkBottom 行。
#pragma once

namespace market_panel_layout {

inline constexpr int kWidth = 400;
inline constexpr int kHeight = 300;
inline constexpr int kTextBaseline = 17;
inline constexpr int kInkTop = 4;
inline constexpr int kInkBottom = 18;

inline constexpr int kHeaderBarBottom = 23;   // 顶部黑条占 y=0..23
inline constexpr int kHeaderTextY = 1;
inline constexpr int kPageLeft = 6;
inline constexpr int kPageRight = 394;        // 文字与线条的右边界（含）
inline constexpr int kColumnHeaderY = 24;
inline constexpr int kColumnRuleY = 45;
inline constexpr int kRowTop = 47;
inline constexpr int kRowPitch = 19;
inline constexpr int kTagX0 = 4;              // 市场色块（黑底白字）
inline constexpr int kTagX1 = 23;
inline constexpr int kTagPadTop = kInkTop - 1;
inline constexpr int kNameX = 29;
inline constexpr int kPriceRight = 181;       // 各数值列右对齐的右边界（不含）
inline constexpr int kPctRight = 246;
inline constexpr int kVolumeRight = 317;
inline constexpr int kTurnoverRight = 395;
inline constexpr int kFooterRuleY = 277;
inline constexpr int kFooterTextY = 279;
// 右下角状态区：设备在这里写「每分钟更新 / N 分钟未更新 / 读取 HH:MM」。
// 服务器位图在这一块留白，设备显示服务器位图时先把它清成白底再写状态。
inline constexpr int kFooterStatusLeft = 194;  // 状态区左边界（含）
inline constexpr int kFooterStatusRight = 388; // 状态文字的右边界（不含）
inline constexpr int kSeparatorStep = 2;      // 组间点线每隔 2px 一个点

inline constexpr const char *kTitleText = "全球行情";
inline constexpr const char *kColumnName = "指数";
inline constexpr const char *kColumnPrice = "最新";
inline constexpr const char *kColumnPct = "涨跌幅";
inline constexpr const char *kColumnVolume = "成交量";
inline constexpr const char *kColumnTurnover = "成交额";
// 页脚右侧的状态文字（直连行情时由设备自绘）：
//   刷新及时            每分钟更新
//   超过 3 分钟没刷新    12 分钟未更新
//   因低电量 / 离线停更   低电量 暂停更新 / 离线模式 暂停更新
inline constexpr const char *kStatusEveryMinute = "每分钟更新";
inline constexpr const char *kStatusStaleSuffix = " 分钟未更新";
inline constexpr const char *kStatusLowBattery = "低电量 暂停更新";
inline constexpr const char *kStatusOffline = "离线模式 暂停更新";
// 位图兜底时右下角覆盖层的前缀，后面接读取时间 HH:MM。
inline constexpr const char *kBitmapReadPrefix = "读取 ";
// 还没有任何行情可显示时的占位提示（LVGL 标签，整段用 Regular 字库）。
inline constexpr const char *kPlaceholderText = "行情数据尚未获取";
inline constexpr const char *kPlaceholderHint = "BOOT 立即刷新 · 长按 KEY 进入设置";

constexpr int row_y(int row)
{
    return kRowTop + row * kRowPitch;
}

struct TagGeometry {
    int block_top;
    int block_bottom;
    int label_y;
    int separator_y;
};

// 一个市场分组的色块与点线位置：色块上下包住本组文字墨迹，
// 相邻两组色块之间留 3px 白缝，点线落在白缝正中。
constexpr TagGeometry tag_geometry(int first_row, int row_count)
{
    const int first_y = row_y(first_row);
    const int last_y = row_y(first_row + row_count - 1);
    const int block_top = first_y + kTagPadTop;
    const int block_bottom = last_y + kInkBottom;
    const int center = (block_top + block_bottom) / 2;
    return {block_top, block_bottom, center - (kInkTop + kInkBottom) / 2, first_y + 1};
}

static_assert(row_y(11) + kInkBottom < kFooterRuleY,
              "the last quote row must stay above the footer rule");
static_assert(kFooterTextY + kInkBottom < kHeight, "footer text must fit on the panel");
static_assert(kFooterStatusLeft < kFooterStatusRight && kFooterStatusRight <= kWidth,
              "footer status box must be a valid span");
static_assert(kTagX1 < kNameX && kPriceRight < kPctRight && kPctRight < kVolumeRight &&
                  kVolumeRight < kTurnoverRight && kTurnoverRight <= kWidth,
              "panel columns must stay ordered left to right");

} // namespace market_panel_layout
