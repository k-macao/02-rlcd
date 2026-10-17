// 集中发布章鱼 AI 简报位图、同步时间与页面请求，避免读到混合状态。
#include "aibrief_state.h"

#include "app_constexpr.h"
#include "scoped_semaphore_lock.h"
#include "ui_task_notify.h"

#include <esp_attr.h>

#include <atomic>
#include <string.h>

namespace {
StaticTaskMutex s_aibrief_mutex;
AibriefSnapshot s_aibrief_state;
AibriefPageRequestSnapshot s_aibrief_page_state;
std::atomic<bool> s_aibrief_page_requested{false};
EXT_RAM_BSS_ATTR uint8_t s_aibrief_bitmap[aibrief::kBitmapBytes];

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
    return s_aibrief_mutex.init();
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
    if (!out || out_len < aibrief::kBitmapBytes) {
        return false;
    }
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock || !s_aibrief_state.available) {
        return false;
    }
    memcpy(out, s_aibrief_bitmap, aibrief::kBitmapBytes);
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
    {
        ScopedSemaphoreLock lock(s_aibrief_mutex);
        if (!lock) {
            return false;
        }
        memcpy(s_aibrief_bitmap, bitmap, aibrief::kBitmapBytes);
        s_aibrief_state.available = true;
        s_aibrief_state.live_source = live_source;
        s_aibrief_state.last_sync = sync_time;
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
        s_aibrief_state.last_sync = sync_time;
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
    memset(s_aibrief_bitmap, 0, sizeof(s_aibrief_bitmap));
    return true;
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
