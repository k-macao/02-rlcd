// 集中保存设备直连行情接口得到的最新行情、各行更新时刻与刷新节奏，
// 避免界面任务读到联网任务写了一半的数据。
#include "market_panel_state.h"

#include "scoped_semaphore_lock.h"
#include "ui_task_notify.h"

#include <esp_timer.h>

namespace {
StaticTaskMutex s_market_panel_mutex;
MarketPanelStatus s_market_panel_status;
market_panel::Quote s_market_panel_rows[market_panel::kRowCount];
// 各行最近一次拿到新数据的开机秒数；该行没有数据时为 0。
uint32_t s_market_panel_row_updated_at[market_panel::kRowCount] = {};
bool s_market_panel_requested = false;
uint32_t s_market_panel_last_request = 0;

void copy_rows(market_panel::Quote out[market_panel::kRowCount],
               const market_panel::Quote in[market_panel::kRowCount])
{
    for (int row = 0; row < market_panel::kRowCount; ++row) {
        out[row] = in[row];
    }
}
} // namespace

bool market_panel_state_init()
{
    return s_market_panel_mutex.init();
}

uint32_t market_panel_uptime_seconds()
{
    return static_cast<uint32_t>(esp_timer_get_time() / 1000000LL);
}

MarketPanelStatus market_panel_status_load()
{
    ScopedSemaphoreLock lock(s_market_panel_mutex);
    if (!lock) {
        return MarketPanelStatus{};
    }
    return s_market_panel_status;
}

bool market_panel_rows_copy(market_panel::Quote rows[market_panel::kRowCount],
                            uint32_t *version)
{
    if (!rows || !version) {
        return false;
    }
    ScopedSemaphoreLock lock(s_market_panel_mutex);
    if (!lock || !s_market_panel_status.available) {
        return false;
    }
    copy_rows(rows, s_market_panel_rows);
    *version = s_market_panel_status.version;
    return true;
}

bool market_panel_mark_refresh_request(uint32_t now_seconds, bool manual)
{
    ScopedSemaphoreLock lock(s_market_panel_mutex);
    if (!lock) {
        return false;
    }
    const bool due = manual
                         ? market_panel::manual_refresh_allowed(s_market_panel_requested,
                                                                s_market_panel_last_request,
                                                                now_seconds)
                         : market_panel::refresh_due(s_market_panel_requested,
                                                     s_market_panel_last_request,
                                                     now_seconds,
                                                     s_market_panel_status.failures);
    if (!due) {
        return false;
    }
    s_market_panel_requested = true;
    s_market_panel_last_request = now_seconds;
    return true;
}

bool market_panel_previous_rows(market_panel::Quote rows[market_panel::kRowCount],
                                uint32_t updated_at[market_panel::kRowCount])
{
    if (!rows || !updated_at) {
        return false;
    }
    ScopedSemaphoreLock lock(s_market_panel_mutex);
    if (!lock) {
        return false;
    }
    copy_rows(rows, s_market_panel_rows);
    for (int row = 0; row < market_panel::kRowCount; ++row) {
        updated_at[row] = s_market_panel_row_updated_at[row];
    }
    return true;
}

bool market_panel_publish(const market_panel::Quote rows[market_panel::kRowCount],
                          const uint32_t updated_at[market_panel::kRowCount],
                          uint32_t now_seconds,
                          time_t wall_now)
{
    if (!rows || !updated_at) {
        return false;
    }
    // 行情只有联网任务（本函数的调用方）会写，所以比较新旧内容不必持锁；
    // 数字格式化较慢，放在锁外，界面任务不用等。
    const bool changed = !s_market_panel_status.available ||
                         !market_panel::panel_text_equal(s_market_panel_rows, rows);
    {
        ScopedSemaphoreLock lock(s_market_panel_mutex);
        if (!lock) {
            return false;
        }
        copy_rows(s_market_panel_rows, rows);
        for (int row = 0; row < market_panel::kRowCount; ++row) {
            s_market_panel_row_updated_at[row] = updated_at[row];
        }
        s_market_panel_status.available = true;
        s_market_panel_status.refreshed_uptime = now_seconds;
        s_market_panel_status.refreshed_wall = wall_now;
        s_market_panel_status.failures = 0;
        if (changed) {
            ++s_market_panel_status.version;
            if (s_market_panel_status.version == 0) {
                ++s_market_panel_status.version;
            }
        }
    }
    // 内容没变也通知界面：页脚的「N 分钟未更新」要立刻换回「每分钟更新」。
    notify_ui_task();
    return true;
}

void market_panel_record_failure()
{
    ScopedSemaphoreLock lock(s_market_panel_mutex);
    if (!lock) {
        return;
    }
    if (s_market_panel_status.failures < UINT8_MAX) {
        ++s_market_panel_status.failures;
    }
}
