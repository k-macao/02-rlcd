// 提供行情数据面板（设备直连行情接口）的行情缓存与刷新节奏的线程安全接口。
// 联网任务发布行情，界面任务读取状态并决定何时请求下一轮刷新。
#pragma once

#include "market_panel_contract.h"

#include <stdint.h>
#include <time.h>

struct MarketPanelStatus {
    // 至少成功刷新过一次，行情可以上屏。
    bool available = false;
    // 屏幕内容每变化一次加一（不会回到 0）；内容没变的刷新只前进时间戳。
    uint32_t version = 0;
    // 最近一次成功刷新的时刻：开机秒数用来判断新鲜度（不受对时影响），
    // 系统时间只用来和服务器位图的读取时间比较先后。
    uint32_t refreshed_uptime = 0;
    time_t refreshed_wall = 0;
    // 连续失败的轮数；成功一次即清零。
    uint8_t failures = 0;
};

bool market_panel_state_init();
// 开机以来的秒数；行情面板的所有节奏判断都用它。
uint32_t market_panel_uptime_seconds();
// 界面任务每次轮询读取：只有几个标量，不复制行情。
MarketPanelStatus market_panel_status_load();
// 复制当前行情及其版本号；还没有行情时返回 false。
bool market_panel_rows_copy(market_panel::Quote rows[market_panel::kRowCount],
                            uint32_t *version);

// 界面任务调用：到了刷新周期（或手动刷新且离上次请求够久）就记下请求时刻并返回 true，
// 调用方随后唤醒联网任务；返回 false 表示这次不必请求。
bool market_panel_mark_refresh_request(uint32_t now_seconds, bool manual);

// 联网任务调用：取上一版行情和各行的更新时刻，供「单个数据源失败时沿用上一版」使用。
bool market_panel_previous_rows(market_panel::Quote rows[market_panel::kRowCount],
                                uint32_t updated_at[market_panel::kRowCount]);
// 发布一轮成功的刷新。屏幕内容与上一版完全相同时不增加版本号。
bool market_panel_publish(const market_panel::Quote rows[market_panel::kRowCount],
                          const uint32_t updated_at[market_panel::kRowCount],
                          uint32_t now_seconds,
                          time_t wall_now);
// 记一次失败：保留上一版行情，连续失败达到阈值后刷新周期退避到 5 分钟。
void market_panel_record_failure();
