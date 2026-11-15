// 声明服务器渲染位图（行情面板兜底画面）的下载入口。
#pragma once

#include <stdbool.h>

// 在已连上 Wi-Fi 的联网窗口里调用：比对清单，内容有变化才下载并发布位图。
// 何时调用由 market_panel_service 决定（直连行情失败且距上次尝试满 5 分钟）。
bool perform_aibrief_update();
