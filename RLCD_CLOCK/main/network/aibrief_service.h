// 声明章鱼 AI 简报的联网刷新入口与新鲜度判断。
#pragma once

#include <stdbool.h>

bool perform_aibrief_update();
void request_aibrief_refresh();
bool aibrief_cache_refresh_due();
