// 下载、校验并发布章鱼 AI 简报页位图（二进制安全，不经文本解码）。
#include "aibrief_service.h"

#include "aibrief_contract.h"
#include "aibrief_state.h"
#include "app_event_group.h"
#include "app_metadata.h"
#include "app_network_config.h"
#include "network_http_transaction_lock.h"
#include "network_sync_wait.h"
#include "scoped_heap_buffer.h"

#include <esp_crt_bundle.h>
#include <esp_http_client.h>
#include <esp_log.h>

#include <string.h>
#include <time.h>

namespace {
#define AIBRIEF_HTTP_OPEN_FAILED_FORMAT "aibrief http failed err=%s attempt=%d"
#define AIBRIEF_HTTP_STATUS_FORMAT "aibrief http status=%d bytes=%u"
#define AIBRIEF_PARSE_FAILED_FORMAT "aibrief parse failed status=%d bytes=%u"
#define AIBRIEF_UPDATED_LOG "aibrief bitmap updated"
#define AIBRIEF_BUFFER_ALLOC_FAILED_LOG "aibrief download buffer alloc failed"
#define AIBRIEF_LOCK_FAILED_LOG "aibrief http lock unavailable"
#define AIBRIEF_CLIENT_INIT_FAILED_LOG "aibrief http client init failed"

constexpr const char *kAibriefRejectedLog = "aibrief bitmap rejected";
constexpr int kAibriefHttpTimeoutMs = 15 * 1000;

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

aibrief::ParseResult parse_download(const uint8_t *body,
                                    size_t body_len,
                                    uint8_t *bitmap)
{
    return aibrief::parse_payload(body, body_len, bitmap);
}

bool settle_before_retry()
{
    return wait_for_network_sync_settle(aibrief::kRetrySettleMs);
}
} // namespace

bool perform_aibrief_update()
{
    time_t now = 0;
    time(&now);
    ScopedHeapBuffer<uint8_t> body(aibrief::kPayloadBytes,
                                   HeapBufferInit::kZeroed,
                                   HeapBufferStorage::kPsramPreferred);
    ScopedHeapBuffer<uint8_t> bitmap(aibrief::kBitmapBytes,
                                     HeapBufferInit::kZeroed,
                                     HeapBufferStorage::kPsramPreferred);
    if (!body || !bitmap) {
        ESP_LOGW(TAG, "%s", AIBRIEF_BUFFER_ALLOC_FAILED_LOG);
        aibrief_publish_failure(now);
        return false;
    }
    for (int attempt = 1; attempt <= aibrief::kMaxRefreshAttempts; ++attempt) {
        body.clear();
        size_t received = 0;
        esp_err_t download_err =
            http_get_binary(kAibriefUrl, body.get(), body.size(), &received);
        if (download_err != ESP_OK) {
            // 主源失败时回退到 jsDelivr 镜像，再失败才放弃本轮。
            download_err = http_get_binary(
                kAibriefBackupUrl, body.get(), body.size(), &received);
        }
        const bool downloaded = download_err == ESP_OK;
        const aibrief::ParseResult parsed =
            downloaded ? parse_download(body.get(), received, bitmap.get())
                       : aibrief::ParseResult::kShortPayload;
        if (downloaded && parsed == aibrief::ParseResult::kOk) {
            if (!aibrief_publish_bitmap(bitmap.get(), bitmap.size(), true, now)) {
                ESP_LOGW(TAG, "%s", kAibriefRejectedLog);
                return false;
            }
            ESP_LOGI(TAG, "%s", AIBRIEF_UPDATED_LOG);
            return true;
        }
        if (downloaded) {
            ESP_LOGW(TAG, AIBRIEF_PARSE_FAILED_FORMAT,
                     static_cast<int>(parsed),
                     static_cast<unsigned>(received));
        } else {
            ESP_LOGW(TAG, AIBRIEF_HTTP_OPEN_FAILED_FORMAT,
                     esp_err_to_name(download_err), attempt);
        }
        if (attempt < aibrief::kMaxRefreshAttempts && !settle_before_retry()) {
            break;
        }
    }
    aibrief_publish_failure(now);
    return false;
}

void request_aibrief_refresh()
{
    app_event_group_set_bits(kAibriefSyncBit);
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
