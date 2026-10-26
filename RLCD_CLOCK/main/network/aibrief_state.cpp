// 集中发布章鱼 AI 简报位图、同步时间与页面请求，避免读到混合状态。
// last_sync 记录最近一次尝试；last_success 只在成功发布位图时前进。
// v1.8+ 支持多页：最多 kMaxPages（255）页，每页 30 秒轮播。
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
// 堆分配：255×15000=3825000 字节（≈3.65MB），放在 PSRAM，避免 BSS 段过大导致链接失败。
// 下载直接写进这块缓存，全流程只有一份位图，PSRAM 峰值比「下载缓冲 + 缓存」少 3.65MB。
uint8_t* s_aibrief_bitmaps = nullptr;
// 上次成功发布的清单 sha256（服务器 aibrief/version.txt）：内容没变就不重复下载。
char s_aibrief_last_sha[aibrief::kManifestShaChars + 1] = {};

inline uint8_t* bitmap_at(size_t index)
{
    if (!s_aibrief_bitmaps) return nullptr;
    return s_aibrief_bitmaps + index * aibrief::kBitmapBytes;
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
            heap_caps_malloc(static_cast<size_t>(aibrief::kMaxPages) * aibrief::kBitmapBytes,
                             MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
        if (!s_aibrief_bitmaps) {
            // 回退到普通堆（可能不足，但尝试）
            s_aibrief_bitmaps = static_cast<uint8_t*>(
                heap_caps_malloc(static_cast<size_t>(aibrief::kMaxPages) * aibrief::kBitmapBytes,
                                 MALLOC_CAP_8BIT));
        }
        if (s_aibrief_bitmaps) {
            memset(s_aibrief_bitmaps, 0,
                   static_cast<size_t>(aibrief::kMaxPages) * aibrief::kBitmapBytes);
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
        // 注意：页数可达 255，循环变量必须用 size_t，用 uint8_t 会回绕成死循环。
        for (size_t i = 0; i < page_count; ++i) {
            memcpy(bitmap_at(i),
                   bitmaps + i * aibrief::kBitmapBytes,
                   aibrief::kBitmapBytes);
        }
        // 清理未使用的页
        for (size_t i = page_count; i < static_cast<size_t>(aibrief::kMaxPages); ++i) {
            memset(bitmap_at(i), 0, aibrief::kBitmapBytes);
        }
        // 内容更新时保留当前页（越界则回到第 1 页）：每页停留时间很长，
        // 若每次刷新都跳回第 1 页，读者永远读不到后面的页。
        const uint8_t keep_page =
            s_aibrief_state.available && s_aibrief_state.current_page < page_count
                ? s_aibrief_state.current_page
                : 0;
        s_aibrief_state.available = true;
        s_aibrief_state.live_source = live_source;
        s_aibrief_state.unchanged = false;
        aibrief_record_sync_time(s_aibrief_state, sync_time, true);
        s_aibrief_state.page_count = page_count;
        s_aibrief_state.current_page = keep_page;
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

uint8_t *aibrief_bitmap_buffer(size_t *out_capacity)
{
    if (out_capacity) {
        *out_capacity = 0;
    }
    if (!s_aibrief_bitmaps) {
        return nullptr;
    }
    if (out_capacity) {
        *out_capacity = static_cast<size_t>(aibrief::kMaxPages) * aibrief::kBitmapBytes;
    }
    return s_aibrief_bitmaps;
}

bool aibrief_publish_bitmaps_inplace(uint8_t page_count, bool live_source, time_t sync_time)
{
    if (!s_aibrief_bitmaps || page_count == 0 || page_count > aibrief::kMaxPages) {
        return false;
    }
    {
        ScopedSemaphoreLock lock(s_aibrief_mutex);
        if (!lock) {
            return false;
        }
        // 位图已经按页序列写进缓存，这里只发布元数据，不再复制一遍。
        for (size_t i = page_count; i < static_cast<size_t>(aibrief::kMaxPages); ++i) {
            memset(bitmap_at(i), 0, aibrief::kBitmapBytes);
        }
        const uint8_t keep_page =
            s_aibrief_state.available && s_aibrief_state.current_page < page_count
                ? s_aibrief_state.current_page
                : 0;
        s_aibrief_state.available = true;
        s_aibrief_state.live_source = live_source;
        s_aibrief_state.unchanged = false;
        aibrief_record_sync_time(s_aibrief_state, sync_time, true);
        s_aibrief_state.page_count = page_count;
        s_aibrief_state.current_page = keep_page;
        ++s_aibrief_state.version;
        if (s_aibrief_state.version == 0) {
            ++s_aibrief_state.version;
        }
    }
    notify_ui_task();
    return true;
}

void aibrief_invalidate_bitmaps()
{
    {
        ScopedSemaphoreLock lock(s_aibrief_mutex);
        if (lock) {
            // 下载途中失败时缓存可能只写了一半：直接作废，回到占位提示，等下一轮重下。
            s_aibrief_state.available = false;
            s_aibrief_state.page_count = 0;
            s_aibrief_state.current_page = 0;
        }
    }
    notify_ui_task();
}

bool aibrief_last_sha(char *out, size_t out_len)
{
    if (!out || out_len == 0) {
        return false;
    }
    out[0] = '\0';
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock || s_aibrief_last_sha[0] == '\0') {
        return false;
    }
    size_t i = 0;
    for (; i + 1 < out_len && s_aibrief_last_sha[i] != '\0'; ++i) {
        out[i] = s_aibrief_last_sha[i];
    }
    out[i] = '\0';
    return i > 0;
}

void aibrief_set_last_sha(const char *sha256)
{
    if (!sha256) {
        return;
    }
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock) {
        return;
    }
    size_t i = 0;
    for (; i < aibrief::kManifestShaChars && sha256[i] != '\0'; ++i) {
        s_aibrief_last_sha[i] = sha256[i];
    }
    s_aibrief_last_sha[i] = '\0';
}

bool aibrief_record_unchanged(time_t sync_time)
{
    {
        ScopedSemaphoreLock lock(s_aibrief_mutex);
        if (!lock) {
            return false;
        }
        s_aibrief_state.live_source = true;
        s_aibrief_state.unchanged = true;
        // 内容没变：只前进时间戳，保留当前页、位图与版本号（屏幕不闪、不重置轮播）。
        aibrief_record_sync_time(s_aibrief_state, sync_time, true);
    }
    notify_ui_task();
    return true;
}

bool aibrief_clear_cache()
{
    ScopedSemaphoreLock lock(s_aibrief_mutex);
    if (!lock) {
        return false;
    }
    s_aibrief_state = AibriefSnapshot{};
    s_aibrief_last_sha[0] = '\0';
    if (s_aibrief_bitmaps) {
        memset(s_aibrief_bitmaps, 0,
               static_cast<size_t>(aibrief::kMaxPages) * aibrief::kBitmapBytes);
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
