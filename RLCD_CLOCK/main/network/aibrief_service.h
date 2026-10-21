// 声明章鱼 AI 简报的联网刷新入口与新鲜度判断。
#pragma once

#include <stdbool.h>

bool perform_aibrief_update();
void request_aibrief_refresh();
// 缓存过期时请求刷新；尚无缓存时按刷新周期节流重试，避免掉线时反复踢网络任务。
void request_aibrief_refresh_if_due();
bool aibrief_cache_refresh_due();
