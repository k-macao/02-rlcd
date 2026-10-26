// 下载、校验并发布章鱼 AI 简报页位图（二进制安全，不经文本解码）。
// v1.8+ 支持多页：单文件包含多张 400x300 位图，设备每 20 秒轮播一页。
#include "aibrief_service.h"

#include "aibrief_contract.h"
#include "aibrief_state.h"
#include "app_event_group.h"
#include "app_metadata.h"
#include "app_network_config.h"
#include "network_http_transaction_lock.h"
#include "network_sync_wait.h"

#include <esp_crt_bundle.h>
#include <esp_http_client.h>
#include <esp_log.h>

#include <string.h>
#include <time.h>

namespace {
#define AIBRIEF_HTTP_OPEN_FAILED_FORMAT "aibrief http failed err=%s attempt=%d"
#define AIBRIEF_HTTP_STATUS_FORMAT "aibrief http status=%d bytes=%u"
#define AIBRIEF_PARSE_FAILED_FORMAT "aibrief parse failed status=%d bytes=%u pages=%u"
#define AIBRIEF_UPDATED_LOG "aibrief bitmap updated pages=%u"
#define AIBRIEF_BUFFER_ALLOC_FAILED_LOG "aibrief bitmap buffer unavailable"
#define AIBRIEF_LOCK_FAILED_LOG "aibrief http lock unavailable"
#define AIBRIEF_CLIENT_INIT_FAILED_LOG "aibrief http client init failed"
#define AIBRIEF_UNCHANGED_LOG "aibrief unchanged pages=%u"
#define AIBRIEF_MANIFEST_LOG "aibrief manifest pages=%u bytes=%u sha=%s"
#define AIBRIEF_MANIFEST_FAILED_LOG "aibrief manifest unavailable err=%s"

constexpr const char *kAibriefRejectedLog = "aibrief bitmap rejected";
// 全量包可达 3.65MB，按 30s 读超时容易在弱网下中断，放宽到 60s。
constexpr int kAibriefHttpTimeoutMs = 60 * 1000;
constexpr int kAibriefManifestTimeoutMs = 15 * 1000;

struct DownloadContext {
    uint8_t *data = nullptr;
    size_t capacity = 0;
    size_t received = 0;
    bool overflow = false;
};

esp_err_t download_event_handler(esp_http_client_event_t *event)
{
    if (!event || event->event_id != HTTP_EVENT_ON_DATA || !event->user_data) {
        return ESP_OK;
    }
    DownloadContext *ctx = static_cast<DownloadContext *>(event->user_data);
    if (!event->data || event->data_len <= 0) {
        return ESP_OK;
    }
    const size_t incoming = static_cast<size_t>(event->data_len);
    if (ctx->received + incoming > ctx->capacity) {
        ctx->overflow = true;
        return ESP_OK;
    }
    memcpy(ctx->data + ctx->received, event->data, incoming);
    ctx->received += incoming;
    return ESP_OK;
}

esp_err_t http_get_binary(const char *url,
                          uint8_t *out,
                          size_t capacity,
                          size_t *received)
{
    if (received) {
        *received = 0;
    }
    if (!url || !out || capacity == 0 || !received) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!acquire_network_http_transaction_lock(
            pdMS_TO_TICKS(kAibriefHttpTimeoutMs))) {
        ESP_LOGW(TAG, "%s", AIBRIEF_LOCK_FAILED_LOG);
        return ESP_ERR_TIMEOUT;
    }
    DownloadContext ctx;
    ctx.data = out;
    ctx.capacity = capacity;
    esp_http_client_config_t config = {};
    config.url = url;
    config.event_handler = download_event_handler;
    config.user_data = &ctx;
    config.timeout_ms = kAibriefHttpTimeoutMs;
    config.crt_bundle_attach = esp_crt_bundle_attach;
    esp_err_t result = ESP_FAIL;
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        ESP_LOGW(TAG, "%s", AIBRIEF_CLIENT_INIT_FAILED_LOG);
        result = ESP_ERR_NO_MEM;
    } else {
        (void)esp_http_client_set_header(client, "Accept", "application/octet-stream");
        const esp_err_t err = esp_http_client_perform(client);
        const int status = esp_http_client_get_status_code(client);
        if (err == ESP_OK && status == 200 && !ctx.overflow) {
            result = ESP_OK;
            *received = ctx.received;
        } else {
            ESP_LOGW(TAG, AIBRIEF_HTTP_STATUS_FORMAT, status,
                     static_cast<unsigned>(ctx.received));
            result = err == ESP_OK ? ESP_ERR_INVALID_RESPONSE : err;
        }
        esp_http_client_cleanup(client);
    }
    release_network_http_transaction_lock();
    return result;
}

// 清单很小（几十字节），单独用短超时拉取，失败就回退整包下载。
esp_err_t http_get_text(const char *url,
                        char *out,
                        size_t capacity,
                        size_t *received)
{
    if (received) {
        *received = 0;
    }
    if (!url || !out || capacity < 2 || !received) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!acquire_network_http_transaction_lock(
            pdMS_TO_TICKS(kAibriefManifestTimeoutMs))) {
        ESP_LOGW(TAG, "%s", AIBRIEF_LOCK_FAILED_LOG);
        return ESP_ERR_TIMEOUT;
    }
    DownloadContext ctx;
    ctx.data = reinterpret_cast<uint8_t *>(out);
    ctx.capacity = capacity - 1; // 预留结尾 0
    esp_http_client_config_t config = {};
    config.url = url;
    config.event_handler = download_event_handler;
    config.user_data = &ctx;
    config.timeout_ms = kAibriefManifestTimeoutMs;
    config.crt_bundle_attach = esp_crt_bundle_attach;
    esp_err_t result = ESP_FAIL;
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        ESP_LOGW(TAG, "%s", AIBRIEF_CLIENT_INIT_FAILED_LOG);
        result = ESP_ERR_NO_MEM;
    } else {
        (void)esp_http_client_set_header(client, "Accept", "text/plain");
        const esp_err_t err = esp_http_client_perform(client);
        const int status = esp_http_client_get_status_code(client);
        if (err == ESP_OK && status == 200 && !ctx.overflow) {
            out[ctx.received] = '\0';
            *received = ctx.received;
            result = ESP_OK;
        } else {
            ESP_LOGW(TAG, AIBRIEF_HTTP_STATUS_FORMAT, status,
                     static_cast<unsigned>(ctx.received));
            result = err == ESP_OK ? ESP_ERR_INVALID_RESPONSE : err;
        }
        esp_http_client_cleanup(client);
    }
    release_network_http_transaction_lock();
    return result;
}

// 流式下载：边收边写进位图缓存，8 字节信封先校验魔数与尺寸，
// 因此不再需要第二份同样大小的下载缓冲（省 3.65MB PSRAM）。
struct StreamContext {
    uint8_t *bitmaps = nullptr;
    size_t capacity = 0;
    size_t bitmaps_len = 0;
    uint8_t header[aibrief::kEnvelopeBytes] = {};
    size_t header_len = 0;
    bool header_ok = false;
    bool overflow = false;
};

esp_err_t stream_event_handler(esp_http_client_event_t *event)
{
    if (!event || event->event_id != HTTP_EVENT_ON_DATA || !event->user_data) {
        return ESP_OK;
    }
    StreamContext *ctx = static_cast<StreamContext *>(event->user_data);
    if (!event->data || event->data_len <= 0 || ctx->overflow) {
        return ESP_OK;
    }
    const uint8_t *data = static_cast<const uint8_t *>(event->data);
    size_t len = static_cast<size_t>(event->data_len);
    if (ctx->header_len < aibrief::kEnvelopeBytes) {
        const size_t take =
            aibrief::kEnvelopeBytes - ctx->header_len < len
                ? aibrief::kEnvelopeBytes - ctx->header_len
                : len;
        memcpy(ctx->header + ctx->header_len, data, take);
        ctx->header_len += take;
        data += take;
        len -= take;
        if (ctx->header_len == aibrief::kEnvelopeBytes) {
            ctx->header_ok =
                memcmp(ctx->header, aibrief::kMagic, sizeof(aibrief::kMagic)) == 0 &&
                aibrief::read_u16_le(ctx->header + 4) == aibrief::kWidth &&
                aibrief::read_u16_le(ctx->header + 6) == aibrief::kHeight;
            if (!ctx->header_ok) {
                ctx->overflow = true; // 信封非法：立刻停止接收
                return ESP_OK;
            }
        }
    }
    if (len == 0) {
        return ESP_OK;
    }
    if (ctx->bitmaps_len + len > ctx->capacity) {
        ctx->overflow = true;
        return ESP_OK;
    }
    memcpy(ctx->bitmaps + ctx->bitmaps_len, data, len);
    ctx->bitmaps_len += len;
    return ESP_OK;
}

esp_err_t http_get_bitmaps(const char *url,
                           uint8_t *bitmaps,
                           size_t capacity,
                           size_t *received,
                           bool *header_ok)
{
    if (received) {
        *received = 0;
    }
    if (header_ok) {
        *header_ok = false;
    }
    if (!url || !bitmaps || capacity == 0 || !received || !header_ok) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!acquire_network_http_transaction_lock(
            pdMS_TO_TICKS(kAibriefHttpTimeoutMs))) {
        ESP_LOGW(TAG, "%s", AIBRIEF_LOCK_FAILED_LOG);
        return ESP_ERR_TIMEOUT;
    }
    StreamContext ctx;
    ctx.bitmaps = bitmaps;
    ctx.capacity = capacity;
    esp_http_client_config_t config = {};
    config.url = url;
    config.event_handler = stream_event_handler;
    config.user_data = &ctx;
    config.timeout_ms = kAibriefHttpTimeoutMs;
    config.crt_bundle_attach = esp_crt_bundle_attach;
    esp_err_t result = ESP_FAIL;
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        ESP_LOGW(TAG, "%s", AIBRIEF_CLIENT_INIT_FAILED_LOG);
        result = ESP_ERR_NO_MEM;
    } else {
        (void)esp_http_client_set_header(client, "Accept", "application/octet-stream");
        const esp_err_t err = esp_http_client_perform(client);
        const int status = esp_http_client_get_status_code(client);
        *header_ok = ctx.header_ok;
        if (err == ESP_OK && status == 200 && ctx.header_ok && !ctx.overflow) {
            *received = ctx.bitmaps_len;
            result = ESP_OK;
        } else {
            ESP_LOGW(TAG, AIBRIEF_HTTP_STATUS_FORMAT, status,
                     static_cast<unsigned>(ctx.bitmaps_len));
            result = err == ESP_OK ? ESP_ERR_INVALID_RESPONSE : err;
        }
        esp_http_client_cleanup(client);
    }
    release_network_http_transaction_lock();
    return result;
}

bool settle_before_retry()
{
    return wait_for_network_sync_settle(aibrief::kRetrySettleMs);
}
} // namespace

namespace {

// 先拉清单，拿到本次发布的 sha256；内容与上次一致时直接跳过整包下载
// （省流量、不重置轮播），返回 true 表示本轮无需下载。
bool manifest_gate(time_t now, aibrief::ManifestInfo *out_info, bool *out_unchanged)
{
    if (out_unchanged) {
        *out_unchanged = false;
    }
    char text[aibrief::kManifestMaxBytes + 1] = {};
    size_t length = 0;
    esp_err_t err = http_get_text(kAibriefManifestUrl, text, sizeof(text), &length);
    if (err != ESP_OK) {
        err = http_get_text(kAibriefManifestBackupUrl, text, sizeof(text), &length);
    }
    if (err != ESP_OK) {
        ESP_LOGD(TAG, AIBRIEF_MANIFEST_FAILED_LOG, esp_err_to_name(err));
        return false;
    }
    const aibrief::ManifestInfo info = aibrief::parse_manifest(text, length);
    if (!info.ok) {
        return false;
    }
    ESP_LOGI(TAG, AIBRIEF_MANIFEST_LOG, static_cast<unsigned>(info.pages),
             static_cast<unsigned>(info.bytes), info.sha256);
    if (out_info) {
        *out_info = info;
    }
    char last[aibrief::kManifestShaChars + 1] = {};
    if (!aibrief_last_sha(last, sizeof(last)) || strcmp(last, info.sha256) != 0) {
        return false; // 尚无基线或内容已更新：需要下载整包
    }
    if (!aibrief_record_unchanged(now)) {
        return false;
    }
    ESP_LOGI(TAG, AIBRIEF_UNCHANGED_LOG, static_cast<unsigned>(info.pages));
    if (out_unchanged) {
        *out_unchanged = true;
    }
    return true;
}

} // namespace

bool perform_aibrief_update()
{
    time_t now = 0;
    time(&now);
    size_t capacity = 0;
    uint8_t *bitmaps = aibrief_bitmap_buffer(&capacity);
    if (!bitmaps || capacity < aibrief::kBitmapBytes) {
        ESP_LOGW(TAG, "%s", AIBRIEF_BUFFER_ALLOC_FAILED_LOG);
        aibrief_publish_failure(now);
        return false;
    }

    aibrief::ManifestInfo manifest;
    bool unchanged = false;
    if (manifest_gate(now, &manifest, &unchanged) && unchanged) {
        return true;
    }

    for (int attempt = 1; attempt <= aibrief::kMaxRefreshAttempts; ++attempt) {
        size_t received = 0;
        bool header_ok = false;
        esp_err_t download_err =
            http_get_bitmaps(kAibriefUrl, bitmaps, capacity, &received, &header_ok);
        if (download_err != ESP_OK) {
            // 主源失败时回退到 jsDelivr 镜像，再失败才放弃本轮。
            download_err = http_get_bitmaps(
                kAibriefBackupUrl, bitmaps, capacity, &received, &header_ok);
        }
        if (download_err == ESP_OK && received >= aibrief::kBitmapBytes &&
            received % aibrief::kBitmapBytes == 0) {
            const size_t pages = received / aibrief::kBitmapBytes;
            if (pages <= static_cast<size_t>(aibrief::kMaxPages)) {
                const uint8_t page_count = static_cast<uint8_t>(pages);
                if (!aibrief_publish_bitmaps_inplace(page_count, true, now)) {
                    ESP_LOGW(TAG, "%s", kAibriefRejectedLog);
                    aibrief_invalidate_bitmaps();
                    return false;
                }
                // 记下本次清单的 sha256：下一轮内容没变就直接跳过下载。
                if (manifest.ok) {
                    aibrief_set_last_sha(manifest.sha256);
                }
                ESP_LOGI(TAG, AIBRIEF_UPDATED_LOG, static_cast<unsigned>(pages));
                return true;
            }
            ESP_LOGW(TAG, AIBRIEF_PARSE_FAILED_FORMAT,
                     static_cast<int>(aibrief::ParseResult::kTooManyPages),
                     static_cast<unsigned>(received), static_cast<unsigned>(pages));
        } else {
            ESP_LOGW(TAG, AIBRIEF_PARSE_FAILED_FORMAT,
                     static_cast<int>(download_err == ESP_OK
                                          ? aibrief::ParseResult::kBadSize
                                          : aibrief::ParseResult::kShortPayload),
                     static_cast<unsigned>(received), 0);
        }
        if (attempt < aibrief::kMaxRefreshAttempts && !settle_before_retry()) {
            break;
        }
    }
    // 半途失败：缓存里可能只有一部分页，作废并回到占位提示，等下一轮重下。
    aibrief_invalidate_bitmaps();
    aibrief_publish_failure(now);
    return false;
}

void request_aibrief_refresh()
{
    app_event_group_set_bits(kAibriefSyncBit);
}

void request_aibrief_refresh_if_due()
{
    if (!aibrief_cache_refresh_due()) {
        return;
    }
    time_t now = 0;
    time(&now);
    const AibriefSnapshot snapshot = aibrief_snapshot_load();
    if (!snapshot.available || snapshot.last_sync <= 0) {
        // 尚无可用缓存时按刷新周期节流重试，掉线也不会每轮都踢网络任务。
        static time_t s_last_cacheless_request = 0;
        const bool throttled = s_last_cacheless_request != 0 &&
                               now >= s_last_cacheless_request &&
                               static_cast<uint32_t>(now - s_last_cacheless_request) <
                                   aibrief::kMaxRefreshAgeSeconds;
        if (throttled) {
            return;
        }
        s_last_cacheless_request = now;
    }
    request_aibrief_refresh();
}

bool aibrief_cache_refresh_due()
{
    const AibriefSnapshot snapshot = aibrief_snapshot_load();
    time_t now = 0;
    time(&now);
    return aibrief::refresh_due(
        static_cast<uint32_t>(snapshot.last_sync > 0 ? snapshot.last_sync : 0),
        static_cast<uint32_t>(now > 0 ? now : 0),
        snapshot.available);
}
