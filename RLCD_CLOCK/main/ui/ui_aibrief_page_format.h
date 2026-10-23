// 格式化 AI 简报右下角的最后成功读取时间与页码；保持纯逻辑便于主机测试。
#pragma once

#include <stddef.h>
#include <stdio.h>
#include <time.h>

struct AibriefReadIndicatorParts {
    char prefix[8];
    char read_time[6];
    char separator[8];
    char current_page[3];
    char page_separator[4];
    char page_count[3];
};

inline void format_aibrief_read_indicator_parts(
    const struct tm *read_time,
    unsigned current_page,
    unsigned page_count,
    AibriefReadIndicatorParts &parts)
{
    parts = AibriefReadIndicatorParts{};
    if (read_time) {
        snprintf(parts.prefix, sizeof(parts.prefix), "读取 ");
        snprintf(parts.read_time, sizeof(parts.read_time), "%02d:%02d",
                 read_time->tm_hour, read_time->tm_min);
        snprintf(parts.separator, sizeof(parts.separator), " · ");
    }
    snprintf(parts.current_page, sizeof(parts.current_page), "%02u", current_page);
    snprintf(parts.page_separator, sizeof(parts.page_separator), " / ");
    snprintf(parts.page_count, sizeof(parts.page_count), "%02u", page_count);
}

inline void format_aibrief_read_indicator(char *out,
                                          size_t out_len,
                                          const struct tm *read_time,
                                          unsigned current_page,
                                          unsigned page_count)
{
    if (!out || out_len == 0) {
        return;
    }
    AibriefReadIndicatorParts parts;
    format_aibrief_read_indicator_parts(read_time, current_page, page_count, parts);
    snprintf(out, out_len, "%s%s%s%s%s%s",
             parts.prefix,
             parts.read_time,
             parts.separator,
             parts.current_page,
             parts.page_separator,
             parts.page_count);
}
