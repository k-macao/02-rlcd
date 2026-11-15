// 生成行情面板页脚右侧的状态文字，并给出「文字是否变化」的重绘键；纯逻辑，便于主机测试。
#pragma once

#include "market_panel_contract.h"
#include "ui_market_panel_layout.h"

#include <stddef.h>
#include <stdint.h>

// 设备主动停止联网刷新的原因；kNone 表示正常按每分钟节奏刷新。
enum class MarketPanelPause : uint8_t {
    kNone,
    kLowBattery,
    kOffline,
};

inline constexpr uint32_t kMarketPanelMaxStaleMinutes = 999;

constexpr uint32_t market_panel_stale_minutes(uint32_t live_age_seconds)
{
    return live_age_seconds / 60U > kMarketPanelMaxStaleMinutes
               ? kMarketPanelMaxStaleMinutes
               : live_age_seconds / 60U;
}

// 状态文字只在这个键变化时才会变化：界面层每次轮询比较键，不变就不重画。
constexpr uint32_t market_panel_status_key(uint32_t live_age_seconds,
                                           MarketPanelPause pause)
{
    if (market_panel::live_view_fresh(live_age_seconds)) {
        return 0;
    }
    if (pause != MarketPanelPause::kNone) {
        return 0x10000U + static_cast<uint32_t>(pause);
    }
    return 1U + market_panel_stale_minutes(live_age_seconds);
}

inline void format_market_panel_status(uint32_t live_age_seconds,
                                       MarketPanelPause pause,
                                       char *out,
                                       size_t out_len)
{
    using market_panel::detail::append_text;
    if (market_panel::live_view_fresh(live_age_seconds)) {
        (void)append_text(out, out_len, 0, market_panel_layout::kStatusEveryMinute);
        return;
    }
    if (pause == MarketPanelPause::kLowBattery) {
        (void)append_text(out, out_len, 0, market_panel_layout::kStatusLowBattery);
        return;
    }
    if (pause == MarketPanelPause::kOffline) {
        (void)append_text(out, out_len, 0, market_panel_layout::kStatusOffline);
        return;
    }
    const uint32_t minutes = market_panel_stale_minutes(live_age_seconds);
    char digits[4] = {};
    size_t count = 0;
    if (minutes >= 100U) {
        digits[count++] = static_cast<char>('0' + minutes / 100U);
    }
    if (minutes >= 10U) {
        digits[count++] = static_cast<char>('0' + minutes / 10U % 10U);
    }
    digits[count++] = static_cast<char>('0' + minutes % 10U);
    digits[count] = '\0';
    const size_t pos = append_text(out, out_len, 0, digits);
    (void)append_text(out, out_len, pos, market_panel_layout::kStatusStaleSuffix);
}

static_assert(market_panel_status_key(0, MarketPanelPause::kNone) ==
                  market_panel_status_key(market_panel::kLiveFreshSeconds - 1,
                                          MarketPanelPause::kLowBattery),
              "a fresh panel shows the same status whatever the pause reason");
static_assert(market_panel_status_key(market_panel::kLiveFreshSeconds,
                                      MarketPanelPause::kNone) !=
                  market_panel_status_key(market_panel::kLiveFreshSeconds + 60,
                                          MarketPanelPause::kNone),
              "the stale minute counter must trigger a redraw every minute");
static_assert(market_panel_stale_minutes(UINT32_MAX) == kMarketPanelMaxStaleMinutes,
              "the stale minute counter saturates at three digits");
