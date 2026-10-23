// 集中发布章鱼 AI 简报位图、同步时间与页面请求，避免读到混合状态。
// last_sync 记录最近一次尝试；last_success 只在成功发布位图时前进。
// v1.8+ 支持多页：最多 kMaxPages 页，每页 1 分钟轮播。
// 修复：大数组改堆分配（PSRAM），避免 EXT_RAM_BSS 链接段溢出。
#include "aibrief_state.h"

#include "app_constexpr.h"
#include "scoped_semaphore_lock.h"
#include "ui_task_notify.h"

#include <esp_attr.h>
#include <esp_heap_caps.h>

#include <atomic>
#include <string.h>

namespace {
StaticTaskMutex s_aibrief_mutex;
AibriefSnapshot s_aibrief_state;
AibriefPageRequestSnapshot s_aibrief_page_state;
std::atomic<bool> s_aibrief_page_requested{false};
// 堆分配：450KB，放在 PSRAM，避免 BSS 段过大导致链接失败
uint8_t* s_aibrief_bitmaps = nullptr;

inline uint8_t* bitmap_at(uint8_t index)
{
    if (!s_aibrief_bitmaps) return nullptr;
    return s_aibrief_bitmaps + static_cast<size_t>(index) * aibrief::kBitmapBytes;
}

void advance_page_revision()
{
    ++s_aibrief_page_state.revision;
    if (s_aibrief_page_state.revision == 0) {
        ++s_aibrief_page_state.revision;
    }
}
} // namespace

bool aibrief_state_init()
{
    if (!s_aibrief_mutex.init()) {
        return false;
    }
    // 分配多页位图缓存，优先 PSRAM
    if (!s_aibrief_bitmaps) {
        s_aibrief_bitmaps = static_cast<uint8_t*>(
            heap_caps_malloc(aibrief::kMaxPages * aibrief::kBitmapBytes,
                             MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
        if (!s_aibrief_bitmaps) {
            // 回退到普通堆（可能不足，但尝试）
            s_aibrief_bitmaps = static_cast<uint8_t*>(
                heap_caps_malloc(aibrief::kMaxPages * aibrief::kBitmapBytes,
                                 MALLOC_CAP_8BIT));
        }
        if (s_aibrief_bitmaps) {
            memset(s_aibrief_bitmaps, 0, aibrief::kMaxPages * aibrief::kBitmapBytes);
        }
    }
    return s_aibrief_bitmaps != nullptr;
}

AibriefSnapshot aibrief_snapshot_load()
{
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock) {
        return AibriefSnapshot{};
    }
    return s_aibrief_state;
}

bool aibrief_bitmap_copy(uint8_t *out, size_t out_len)
{
    return aibrief_bitmap_copy_current(out, out_len);
}

bool aibrief_bitmap_copy_page(uint8_t *out, size_t out_len, uint8_t page_index)
{
    if (!out || out_len < aibrief::kBitmapBytes) {
        return false;
    }
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock || !s_aibrief_state.available || !s_aibrief_bitmaps) {
        return false;
    }
    if (page_index >= s_aibrief_state.page_count) {
        return false;
    }
    memcpy(out, bitmap_at(page_index), aibrief::kBitmapBytes);
    return true;
}

bool aibrief_bitmap_copy_current(uint8_t *out, size_t out_len)
{
    if (!out || out_len < aibrief::kBitmapBytes) {
        return false;
    }
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock || !s_aibrief_state.available || !s_aibrief_bitmaps) {
        return false;
    }
    const uint8_t idx = s_aibrief_state.current_page < s_aibrief_state.page_count
                            ? s_aibrief_state.current_page
                            : 0;
    memcpy(out, bitmap_at(idx), aibrief::kBitmapBytes);
    return true;
}

bool aibrief_publish_bitmap(const uint8_t *bitmap,
                            size_t bitmap_len,
                            bool live_source,
                            time_t sync_time)
{
    if (!bitmap || bitmap_len != aibrief::kBitmapBytes) {
        return false;
    }
    return aibrief_publish_bitmaps(bitmap, bitmap_len, 1, live_source, sync_time);
}

bool aibrief_publish_bitmaps(const uint8_t *bitmaps,
                             size_t bitmaps_len,
                             uint8_t page_count,
                             bool live_source,
                             time_t sync_time)
{
    if (!bitmaps || page_count == 0 || page_count > aibrief::kMaxPages || !s_aibrief_bitmaps) {
        return false;
    }
    const size_t expected = static_cast<size_t>(page_count) * aibrief::kBitmapBytes;
    if (bitmaps_len != expected) {
        return false;
    }
    {
        ScopedSemaphoreLock lock(s_aibrief_mutex);
        if (!lock) {
            return false;
        }
        for (uint8_t i = 0; i < page_count; ++i) {
            memcpy(bitmap_at(i),
                   bitmaps + static_cast<size_t>(i) * aibrief::kBitmapBytes,
                   aibrief::kBitmapBytes);
        }
        // 清理未使用的页
        for (uint8_t i = page_count; i < aibrief::kMaxPages; ++i) {
            memset(bitmap_at(i), 0, aibrief::kBitmapBytes);
        }
        s_aibrief_state.available = true;
        s_aibrief_state.live_source = live_source;
        aibrief_record_sync_time(s_aibrief_state, sync_time, true);
        s_aibrief_state.page_count = page_count;
        s_aibrief_state.current_page = 0;
        ++s_aibrief_state.version;
        if (s_aibrief_state.version == 0) {
            ++s_aibrief_state.version;
        }
    }
    notify_ui_task();
    return true;
}

void aibrief_publish_failure(time_t sync_time)
{
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock) {
        return;
    }
    s_aibrief_state.live_source = false;
    if (s_aibrief_state.available) {
        aibrief_record_sync_time(s_aibrief_state, sync_time, false);
    }
    notify_ui_task();
}

bool aibrief_clear_cache()
{
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock) {
        return false;
    }
    s_aibrief_state = AibriefSnapshot{};
    if (s_aibrief_bitmaps) {
        memset(s_aibrief_bitmaps, 0, aibrief::kMaxPages * aibrief::kBitmapBytes);
    }
    return true;
}

bool aibrief_advance_page()
{
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock || !s_aibrief_state.available || s_aibrief_state.page_count <= 1) {
        return false;
    }
    s_aibrief_state.current_page =
        (s_aibrief_state.current_page + 1) % s_aibrief_state.page_count;
    ++s_aibrief_state.version;
    if (s_aibrief_state.version == 0) {
        ++s_aibrief_state.version;
    }
    return true;
}

bool aibrief_set_current_page(uint8_t page_index)
{
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock || !s_aibrief_state.available) {
        return false;
    }
    if (page_index >= s_aibrief_state.page_count) {
        return false;
    }
    if (s_aibrief_state.current_page == page_index) {
        return false;
    }
    s_aibrief_state.current_page = page_index;
    ++s_aibrief_state.version;
    if (s_aibrief_state.version == 0) {
        ++s_aibrief_state.version;
    }
    return true;
}

uint8_t aibrief_page_count()
{
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock) {
        return 0;
    }
    return s_aibrief_state.page_count;
}

uint8_t aibrief_current_page_index()
{
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock) {
        return 0;
    }
    return s_aibrief_state.current_page;
}

bool aibrief_page_requested()
{
    return s_aibrief_page_requested.load(std::memory_order_acquire);
}

AibriefPageRequestSnapshot aibrief_page_snapshot_load()
{
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock) {
        return AibriefPageRequestSnapshot{};
    }
    return s_aibrief_page_state;
}

void aibrief_page_request()
{
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock) {
        return;
    }
    advance_page_revision();
    s_aibrief_page_state.requested = true;
    s_aibrief_page_requested.store(true, std::memory_order_release);
}

void aibrief_page_clear()
{
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock) {
        return;
    }
    if (!s_aibrief_page_state.requested) {
        return;
    }
    advance_page_revision();
    s_aibrief_page_state.requested = false;
    s_aibrief_page_requested.store(false, std::memory_order_release);
}

bool aibrief_page_clear_if_current(const AibriefPageRequestSnapshot &snapshot)
{
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock) {
        return false;
    }
    if (s_aibrief_page_state.revision != snapshot.revision ||
        s_aibrief_page_state.requested != snapshot.requested) {
        return false;
    }
    advance_page_revision();
    s_aibrief_page_state.requested = false;
    s_aibrief_page_requested.store(false, std::memory_order_release);
    return true;
}
