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

inline unsigned clamp_aibrief_time_component(int value, unsigned maximum)
{
    if (value < 0) {
        return 0U;
    }
    const unsigned component = static_cast<unsigned>(value);
    return component > maximum ? maximum : component;
}

inline void format_aibrief_two_digits(char *out, unsigned value)
{
    // 页面和时间分量均为两位显示；饱和到 99，保证目标缓冲区始终足够。
    if (value > 99U) {
        value = 99U;
    }
    out[0] = static_cast<char>('0' + value / 10U);
    out[1] = static_cast<char>('0' + value % 10U);
    out[2] = '\0';
}

inline void format_aibrief_read_indicator_parts(
    const struct tm *read_time,
    unsigned current_page,
    unsigned page_count,
    AibriefReadIndicatorParts &parts)
{
    parts = AibriefReadIndicatorParts{};
    if (read_time) {
        snprintf(parts.prefix, sizeof(parts.prefix), "读取 ");
        format_aibrief_two_digits(
            parts.read_time, clamp_aibrief_time_component(read_time->tm_hour, 23U));
        parts.read_time[2] = ':';
        format_aibrief_two_digits(
            parts.read_time + 3, clamp_aibrief_time_component(read_time->tm_min, 59U));
        snprintf(parts.separator, sizeof(parts.separator), " · ");
    }
    format_aibrief_two_digits(parts.current_page, current_page);
    snprintf(parts.page_separator, sizeof(parts.page_separator), " / ");
    format_aibrief_two_digits(parts.page_count, page_count);
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
