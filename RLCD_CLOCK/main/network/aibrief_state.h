// 提供主页（行情数据面板）的页面请求、服务器位图缓存和同步状态的线程安全接口。
// 服务器位图是行情面板的兜底画面：设备直连行情接口失败时才下载并显示。
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
    // 行情面板只有一页：有位图时为 1，current_page 恒为 0。
    uint8_t page_count = 0;
    uint8_t current_page = 0;
    // 内容未变化（清单 sha256 相同）时只前进时间戳，不动版本号。
    bool unchanged = false;
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
// 下载缓冲：直接写入位图缓存，不另外申请一份下载缓冲。
// 下载失败时调用 aibrief_invalidate_bitmaps() 作废可能只写了一半的缓存。
uint8_t *aibrief_bitmap_buffer(size_t *out_capacity);
bool aibrief_publish_bitmaps_inplace(uint8_t page_count, bool live_source, time_t sync_time);
void aibrief_invalidate_bitmaps();

// 上次成功发布的清单 sha256（64 位十六进制 + 结尾 0），用于条件请求比对。
bool aibrief_last_sha(char *out, size_t out_len);
void aibrief_set_last_sha(const char *sha256);
// 清单比对结果一致：只更新同步时间与在线标记，保留位图。
bool aibrief_record_unchanged(time_t sync_time);

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

bool aibrief_page_requested();
AibriefPageRequestSnapshot aibrief_page_snapshot_load();
void aibrief_page_request();
void aibrief_page_clear();
bool aibrief_page_clear_if_current(const AibriefPageRequestSnapshot &snapshot);

static_assert(aibrief::kBitmapBytes == 15000,
              "aibrief state caches a single 400x300 1bpp frame");
static_assert(aibrief::kMaxPages <= 255, "page count fits in uint8");
