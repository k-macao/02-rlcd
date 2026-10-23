// 提供章鱼 AI 简报页请求、位图缓存和同步状态的线程安全接口。
// v1.8+ 支持多页：最多 kMaxPages 页，每页 1 分钟自动轮播，完整展示 002 全量内容。
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
    // 仅在位图成功发布后更新；失败重试不会冒充新的读取时间。
    time_t last_success = 0;
    uint8_t page_count = 0;
    uint8_t current_page = 0;
};

inline void aibrief_record_sync_time(AibriefSnapshot &snapshot,
                                     time_t sync_time,
                                     bool succeeded)
{
    snapshot.last_sync = sync_time;
    if (succeeded) {
        snapshot.last_success = sync_time;
    }
}

struct AibriefPageRequestSnapshot {
    bool requested = false;
    uint32_t revision = 0;
};

bool aibrief_state_init();
AibriefSnapshot aibrief_snapshot_load();
bool aibrief_bitmap_copy(uint8_t *out, size_t out_len);
bool aibrief_bitmap_copy_page(uint8_t *out, size_t out_len, uint8_t page_index);
bool aibrief_bitmap_copy_current(uint8_t *out, size_t out_len);
// 发布新位图：兼容单页；多页请用 publish_bitmaps。
bool aibrief_publish_bitmap(const uint8_t *bitmap,
                            size_t bitmap_len,
                            bool live_source,
                            time_t sync_time);
bool aibrief_publish_bitmaps(const uint8_t *bitmaps,
                             size_t bitmaps_len,
                             uint8_t page_count,
                             bool live_source,
                             time_t sync_time);
void aibrief_publish_failure(time_t sync_time);
bool aibrief_clear_cache();

// 分页轮播：每 60 秒自动切下一页，完整展示。
bool aibrief_advance_page();
bool aibrief_set_current_page(uint8_t page_index);
uint8_t aibrief_page_count();
uint8_t aibrief_current_page_index();

bool aibrief_page_requested();
AibriefPageRequestSnapshot aibrief_page_snapshot_load();
void aibrief_page_request();
void aibrief_page_clear();
bool aibrief_page_clear_if_current(const AibriefPageRequestSnapshot &snapshot);

static_assert(aibrief::kBitmapBytes == 15000,
              "aibrief state caches a single 400x300 1bpp frame");
static_assert(aibrief::kMaxPages <= 64, "page count fits in uint8");
