// 格式化 AI 简报右下角的最后成功读取时间与页码；保持纯逻辑便于主机测试。
#pragma once

#include <stddef.h>
#include <stdio.h>
#include <time.h>

inline void format_aibrief_read_indicator(char *out,
                                          size_t out_len,
                                          const struct tm *read_time,
                                          unsigned current_page,
                                          unsigned page_count)
{
    if (!out || out_len == 0) {
        return;
    }
    if (read_time) {
        snprintf(out, out_len, "读取 %02d:%02d · %02u / %02u",
                 read_time->tm_hour,
                 read_time->tm_min,
                 current_page,
                 page_count);
        return;
    }
    snprintf(out, out_len, "%02u / %02u", current_page, page_count);
}
