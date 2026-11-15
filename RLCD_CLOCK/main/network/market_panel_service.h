// 声明行情数据面板的联网刷新入口：设备直连公开行情接口，每分钟取一次最新数据。
#pragma once

#include <stdbool.h>

// 立即请求刷新（开机、从设置进入、BOOT 键）；离上次请求不足 10 秒时忽略。
// 返回 true 表示这次真的排上了一轮刷新。
bool request_market_panel_refresh();
// 到了刷新周期才请求：正常每 60 秒一次，连续失败 3 次后退避到 5 分钟一次。
bool request_market_panel_refresh_if_due();
// 联网任务在已连上 Wi-Fi 的窗口里调用：直连行情接口刷新面板；
// 直连失败时按 5 分钟的节流回退去取服务器渲染的位图。两者任一成功即返回 true。
bool perform_market_panel_window_update();
// 联网窗口没能连上 Wi-Fi：记一次失败，让刷新周期按失败次数退避。
void market_panel_note_window_unavailable();
