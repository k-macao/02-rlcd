// 提供章鱼 AI 简报页请求、位图缓存和同步状态的线程安全接口。
#pragma once

#include "aibrief_contract.h"

#include <stddef.h>
#include <stdint.h>
#include <time.h>

struct AibriefSnapshot {
    bool available = false;
    bool live_source = false;
    uint32_t version = 0;
    time_t last_sync = 0;
};

struct AibriefPageRequestSnapshot {
    bool requested = false;
    uint32_t revision = 0;
};

bool aibrief_state_init();
AibriefSnapshot aibrief_snapshot_load();
bool aibrief_bitmap_copy(uint8_t *out, size_t out_len);
// 发布新位图：仅当参数完整且位图不足时保持旧内容。返回是否受理。
bool aibrief_publish_bitmap(const uint8_t *bitmap,
                            size_t bitmap_len,
                            bool live_source,
                            time_t sync_time);
void aibrief_publish_failure(time_t sync_time);
bool aibrief_clear_cache();

bool aibrief_page_requested();
AibriefPageRequestSnapshot aibrief_page_snapshot_load();
void aibrief_page_request();
void aibrief_page_clear();
bool aibrief_page_clear_if_current(const AibriefPageRequestSnapshot &snapshot);

static_assert(aibrief::kBitmapBytes == 15000,
              "aibrief state caches a single 400x300 1bpp frame");
