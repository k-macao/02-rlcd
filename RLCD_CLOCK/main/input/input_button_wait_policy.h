// 定义按键 GPIO 初始化重试和松开后事件等待的纯判断策略。
#pragma once

#include "input_button_config.h"

constexpr unsigned kButtonGpioConfigMaxAttempts = 3;
constexpr int kButtonGpioConfigRetryDelayMs = 100;

constexpr bool button_gpio_config_retry_due(unsigned completed_attempt,
                                            bool succeeded)
{
    return !succeeded && completed_attempt < kButtonGpioConfigMaxAttempts;
}

constexpr bool button_task_can_wait_for_edge(bool edge_wakeup_ready,
                                             bool boot_pressed,
                                             bool key_pressed,
                                             bool press_tracking_active)
{
    return edge_wakeup_ready &&
           !boot_pressed &&
           !key_pressed &&
           !press_tracking_active;
}

// 简报页的 BOOT 手势：短按翻下一页，长按回上一页，抖动区间不动作。
// 与自动轮播（每 30 秒一次）共用 aibrief::page_step 的环形边界。
enum class AibriefPageButtonAction {
    kNone,
    kNextPage,
    kPreviousPage,
};

constexpr AibriefPageButtonAction aibrief_page_button_action(bool short_press,
                                                             bool long_press)
{
    if (short_press) {
        return AibriefPageButtonAction::kNextPage;
    }
    if (long_press) {
        return AibriefPageButtonAction::kPreviousPage;
    }
    return AibriefPageButtonAction::kNone;
}

constexpr int button_task_poll_delay_ms(bool any_button_pressed,
                                        bool interactive_surface,
                                        bool low_refresh_surface)
{
    if (any_button_pressed) {
        return kButtonPressedPollMs;
    }
    if (interactive_surface) {
        return kButtonActivePollMs;
    }
    if (low_refresh_surface) {
        return kButtonLowRefreshIdlePollMs;
    }
    return kButtonIdlePollMs;
}
