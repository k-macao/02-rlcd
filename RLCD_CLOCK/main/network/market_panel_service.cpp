// 直连公开行情接口刷新行情数据面板：四个 HTTPS GET → 解析 → 合并 → 发布。
//
//   腾讯证券   A 股 / 港股 / 美股指数的点位、涨跌幅、成交量、成交额，日韩指数的点位（主干源）
//   新浪财经   港股指数的成交股数（必须带 Referer，否则 403）
//   Naver 金融 韩国指数的点位 / 成交量 / 成交额，日本指数的点位 / 成交量（两个地址同一主机，
//              复用一条 TLS 连接）
//
// 解析、合并与数字格式都在 market_panel_contract.h，和服务器渲染器
// device_brief/market_quotes.py 共用同一批实测响应做回归。
#include "market_panel_service.h"

#include "aibrief_service.h"
#include "app_event_group.h"
#include "app_metadata.h"
#include "app_network_config.h"
#include "market_panel_contract.h"
#include "market_panel_state.h"
#include "network_http_transaction_lock.h"

#include <esp_crt_bundle.h>
#include <esp_heap_caps.h>
#include <esp_http_client.h>
#include <esp_log.h>
#include <esp_timer.h>

#include <new>
#include <stdint.h>
#include <string.h>
#include <time.h>

namespace {
#define MARKET_PANEL_WORKSPACE_FAILED_LOG "market panel workspace unavailable"
#define MARKET_PANEL_LOCK_FAILED_LOG "market panel http lock unavailable"
#define MARKET_PANEL_CLIENT_INIT_FAILED_LOG "market panel http client init failed"
#define MARKET_PANEL_HTTP_FAILED_FORMAT "market panel %s http failed err=%s status=%d bytes=%u"
#define MARKET_PANEL_PARSE_FAILED_FORMAT "market panel %s parse failed bytes=%u"
#define MARKET_PANEL_UPDATED_FORMAT \
    "market panel updated rows=%d tencent=%d sina=%d naver_kr=%d naver_jp=%d ms=%u"
#define MARKET_PANEL_TOO_FEW_ROWS_FORMAT \
    "market panel rejected rows=%d need=%d tencent=%d naver_kr=%d naver_jp=%d ms=%u"
#define MARKET_PANEL_FALLBACK_LOG "market panel direct fetch failed, trying server bitmap"

constexpr const char *kSourceTencent = "tencent";
constexpr const char *kSourceSina = "sina";
constexpr const char *kSourceNaverKr = "naver_kr";
constexpr const char *kSourceNaverJp = "naver_jp";

// 实测响应：腾讯 3.3KB、Naver 每个 2KB、新浪 0.4KB；留一倍以上余量。
constexpr size_t kResponseBufferBytes = 8 * 1024;
constexpr int kQuoteHttpTimeoutMs = 8 * 1000;
constexpr int kQuoteHttpRxBufferBytes = 2048;
constexpr int kQuoteHttpTxBufferBytes = 1024;
// 主干源失败时同一轮内再试一次；辅助源失败由「沿用上一版」兜住，不重试。
constexpr int kTencentAttempts = 2;
// 腾讯负责 A 股、美股共 6 行且没有替代源：它失败时其余数据源最多凑出 6 行。
constexpr int kTencentOnlyRows = 6;
// 与服务器渲染器 market_quotes.py 的 USER_AGENT 相同。
constexpr const char *kQuoteUserAgent =
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36";

static_assert(market_panel::kRowCount - kTencentOnlyRows < market_panel::kMinLiveRows,
              "without Tencent the panel cannot reach the live row threshold");
static_assert(kResponseBufferBytes >= 2 * 3344,
              "response buffer must keep headroom over the largest measured body");

struct BodyContext {
    char *data = nullptr;
    size_t capacity = 0; // 不含结尾 0
    size_t received = 0;
    bool overflow = false;
};

// 一轮刷新用到的全部缓冲：约 13KB，首次使用时从 PSRAM 申请后常驻，
// 不占联网任务的栈，也不必每分钟反复申请释放。
struct Workspace {
    char body[kResponseBufferBytes];
    market_panel::SourceQuotes tencent;
    market_panel::SourceQuotes sina;
    market_panel::SourceQuotes naver_kr;
    market_panel::SourceQuotes naver_jp;
    market_panel::Quote fresh[market_panel::kRowCount];
    market_panel::Quote previous[market_panel::kRowCount];
    market_panel::Quote merged[market_panel::kRowCount];
    uint32_t previous_at[market_panel::kRowCount];
    uint32_t merged_at[market_panel::kRowCount];
};

Workspace *s_workspace = nullptr;
// 以下状态只在联网任务里读写。
market_panel::SourceHealth s_sina_health;
market_panel::SourceHealth s_naver_health;
bool s_bitmap_fallback_attempted = false;
uint32_t s_bitmap_fallback_last_attempt = 0;

Workspace *workspace()
{
    if (!s_workspace) {
        void *memory = heap_caps_malloc(sizeof(Workspace), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!memory) {
            memory = heap_caps_malloc(sizeof(Workspace), MALLOC_CAP_8BIT);
        }
        if (memory) {
            s_workspace = new (memory) Workspace();
        }
    }
    return s_workspace;
}

esp_err_t body_event_handler(esp_http_client_event_t *event)
{
    if (!event || event->event_id != HTTP_EVENT_ON_DATA || !event->user_data) {
        return ESP_OK;
    }
    BodyContext *body = static_cast<BodyContext *>(event->user_data);
    if (!event->data || event->data_len <= 0 || body->overflow) {
        return ESP_OK;
    }
    const size_t incoming = static_cast<size_t>(event->data_len);
    if (body->received + incoming > body->capacity) {
        body->overflow = true;
        return ESP_OK;
    }
    memcpy(body->data + body->received, event->data, incoming);
    body->received += incoming;
    return ESP_OK;
}

// 一条到行情主机的 HTTPS 连接；同一主机的后续请求复用它（HTTP keep-alive）。
// 整个生命周期内持有全局 HTTPS 事务锁，保证同一时刻只有一个 TLS 会话。
class QuoteConnection {
public:
    explicit QuoteConnection(Workspace &workspace)
    {
        body_.data = workspace.body;
        body_.capacity = sizeof(workspace.body) - 1;
        locked_ = acquire_network_http_transaction_lock(pdMS_TO_TICKS(kQuoteHttpTimeoutMs));
        if (!locked_) {
            ESP_LOGW(TAG, "%s", MARKET_PANEL_LOCK_FAILED_LOG);
        }
    }

    ~QuoteConnection()
    {
        close();
        if (locked_) {
            release_network_http_transaction_lock();
        }
    }

    QuoteConnection(const QuoteConnection &) = delete;
    QuoteConnection &operator=(const QuoteConnection &) = delete;

    // 成功时正文在 workspace.body 里（已补结尾 0），长度写入 *out_len。
    bool get(const char *source, const char *url, const char *referer, size_t *out_len)
    {
        *out_len = 0;
        if (!locked_) {
            return false;
        }
        // 已有连接且换地址成功才算复用；复用的连接可能已被服务器关掉，
        // 所以在复用连接上失败时换一条新连接再试一次。
        bool reused = client_ != nullptr && esp_http_client_set_url(client_, url) == ESP_OK;
        if (!reused) {
            close();
        }
        for (;;) {
            if (!client_ && !open(url, referer)) {
                return false;
            }
            body_.received = 0;
            body_.overflow = false;
            body_.data[0] = '\0';
            const esp_err_t err = esp_http_client_perform(client_);
            const int status = esp_http_client_get_status_code(client_);
            if (err == ESP_OK && status == 200 && !body_.overflow && body_.received > 0) {
                body_.data[body_.received] = '\0';
                *out_len = body_.received;
                return true;
            }
            ESP_LOGW(TAG, MARKET_PANEL_HTTP_FAILED_FORMAT, source, esp_err_to_name(err), status,
                     static_cast<unsigned>(body_.received));
            // 失败的连接里可能还留着半截响应，不能再用。
            close();
            if (!reused) {
                return false;
            }
            reused = false;
        }
    }

    void close()
    {
        if (client_) {
            esp_http_client_cleanup(client_);
            client_ = nullptr;
        }
    }

private:
    bool open(const char *url, const char *referer)
    {
        esp_http_client_config_t config = {};
        config.url = url;
        config.event_handler = body_event_handler;
        config.user_data = &body_;
        config.timeout_ms = kQuoteHttpTimeoutMs;
        config.crt_bundle_attach = esp_crt_bundle_attach;
        config.user_agent = kQuoteUserAgent;
        config.buffer_size = kQuoteHttpRxBufferBytes;
        config.buffer_size_tx = kQuoteHttpTxBufferBytes;
        // 行情接口不会重定向；真遇到 30x 就按失败处理，不把跳转页当行情解析。
        config.disable_auto_redirect = true;
        client_ = esp_http_client_init(&config);
        if (!client_) {
            ESP_LOGW(TAG, "%s", MARKET_PANEL_CLIENT_INIT_FAILED_LOG);
            return false;
        }
        (void)esp_http_client_set_header(client_, "Accept", "*/*");
        // 明确要求不压缩：设备端直接按文本解析。
        (void)esp_http_client_set_header(client_, "Accept-Encoding", "identity");
        if (referer) {
            (void)esp_http_client_set_header(client_, "Referer", referer);
        }
        return true;
    }

    BodyContext body_;
    esp_http_client_handle_t client_ = nullptr;
    bool locked_ = false;
};

using SourceParser = bool (*)(const char *text, size_t length, market_panel::SourceQuotes *out);

bool fetch_and_parse(QuoteConnection &connection,
                     Workspace &ws,
                     const char *source,
                     const char *url,
                     const char *referer,
                     SourceParser parse,
                     market_panel::SourceQuotes *out)
{
    size_t length = 0;
    if (!connection.get(source, url, referer, &length)) {
        return false;
    }
    if (!parse(ws.body, length, out)) {
        ESP_LOGW(TAG, MARKET_PANEL_PARSE_FAILED_FORMAT, source, static_cast<unsigned>(length));
        return false;
    }
    return true;
}

bool fetch_tencent(Workspace &ws)
{
    for (int attempt = 1; attempt <= kTencentAttempts; ++attempt) {
        QuoteConnection connection(ws);
        if (fetch_and_parse(connection, ws, kSourceTencent, kMarketTencentUrl, nullptr,
                            market_panel::parse_tencent, &ws.tencent)) {
            return true;
        }
        ws.tencent = market_panel::SourceQuotes{};
    }
    return false;
}

bool fetch_sina(Workspace &ws)
{
    QuoteConnection connection(ws);
    if (fetch_and_parse(connection, ws, kSourceSina, kMarketSinaHkUrl, kMarketSinaReferer,
                        market_panel::parse_sina_hk, &ws.sina)) {
        return true;
    }
    ws.sina = market_panel::SourceQuotes{};
    return false;
}

// 两个地址同一主机：第二个请求复用第一条连接，省掉一次 TLS 握手。
// 任一地址成功都算 Naver 可用。
bool fetch_naver(Workspace &ws, bool *kr_ok, bool *jp_ok)
{
    QuoteConnection connection(ws);
    *kr_ok = fetch_and_parse(connection, ws, kSourceNaverKr, kMarketNaverKrUrl, nullptr,
                             market_panel::parse_naver, &ws.naver_kr);
    if (!*kr_ok) {
        ws.naver_kr = market_panel::SourceQuotes{};
    }
    *jp_ok = fetch_and_parse(connection, ws, kSourceNaverJp, kMarketNaverJpUrl, nullptr,
                             market_panel::parse_naver, &ws.naver_jp);
    if (!*jp_ok) {
        ws.naver_jp = market_panel::SourceQuotes{};
    }
    return *kr_ok || *jp_ok;
}

uint32_t elapsed_ms_since(int64_t started_us)
{
    const int64_t elapsed_us = esp_timer_get_time() - started_us;
    return elapsed_us > 0 ? static_cast<uint32_t>(elapsed_us / 1000) : 0;
}

bool perform_market_panel_update()
{
    const int64_t started_us = esp_timer_get_time();
    Workspace *ws = workspace();
    if (!ws) {
        ESP_LOGW(TAG, "%s", MARKET_PANEL_WORKSPACE_FAILED_LOG);
        market_panel_record_failure();
        return false;
    }
    ws->tencent = market_panel::SourceQuotes{};
    ws->sina = market_panel::SourceQuotes{};
    ws->naver_kr = market_panel::SourceQuotes{};
    ws->naver_jp = market_panel::SourceQuotes{};

    const bool tencent_ok = fetch_tencent(*ws);
    bool sina_ok = false;
    bool naver_kr_ok = false;
    bool naver_jp_ok = false;
    // 腾讯失败时这一轮注定凑不够行数，不再为辅助源多开两次 TLS。
    if (tencent_ok) {
        const bool sina_due = market_panel::source_fetch_due(s_sina_health);
        if (sina_due) {
            sina_ok = fetch_sina(*ws);
        }
        market_panel::source_record_cycle(&s_sina_health, sina_due, sina_ok);

        const bool naver_due = market_panel::source_fetch_due(s_naver_health);
        bool naver_ok = false;
        if (naver_due) {
            naver_ok = fetch_naver(*ws, &naver_kr_ok, &naver_jp_ok);
        }
        market_panel::source_record_cycle(&s_naver_health, naver_due, naver_ok);
    }

    market_panel::merge_sources(ws->tencent, ws->sina, ws->naver_kr, ws->naver_jp, ws->fresh);
    const int live_rows = market_panel::valid_row_count(ws->fresh);
    if (live_rows < market_panel::kMinLiveRows) {
        ESP_LOGW(TAG, MARKET_PANEL_TOO_FEW_ROWS_FORMAT, live_rows, market_panel::kMinLiveRows,
                 tencent_ok, naver_kr_ok, naver_jp_ok,
                 static_cast<unsigned>(elapsed_ms_since(started_us)));
        market_panel_record_failure();
        return false;
    }

    const uint32_t now_seconds = market_panel_uptime_seconds();
    if (!market_panel_previous_rows(ws->previous, ws->previous_at)) {
        for (int row = 0; row < market_panel::kRowCount; ++row) {
            ws->previous[row] = market_panel::Quote{};
            ws->previous_at[row] = 0;
        }
    }
    market_panel::carry_over_rows(ws->previous, ws->previous_at, ws->fresh, now_seconds,
                                  ws->merged, ws->merged_at);
    time_t wall_now = 0;
    time(&wall_now);
    if (!market_panel_publish(ws->merged, ws->merged_at, now_seconds, wall_now)) {
        market_panel_record_failure();
        return false;
    }
    ESP_LOGI(TAG, MARKET_PANEL_UPDATED_FORMAT, live_rows, tencent_ok, sina_ok, naver_kr_ok,
             naver_jp_ok, static_cast<unsigned>(elapsed_ms_since(started_us)));
    return true;
}

bool request_refresh(bool manual)
{
    if (!market_panel_mark_refresh_request(market_panel_uptime_seconds(), manual)) {
        return false;
    }
    // 沿用原简报页的联网请求位：联网任务据此开一个行情窗口。
    app_event_group_set_bits(kAibriefSyncBit);
    return true;
}
} // namespace

bool request_market_panel_refresh()
{
    return request_refresh(true);
}

bool request_market_panel_refresh_if_due()
{
    return request_refresh(false);
}

bool perform_market_panel_window_update()
{
    const bool live_ok = perform_market_panel_update();
    const uint32_t now_seconds = market_panel_uptime_seconds();
    if (!market_panel::bitmap_fallback_due(live_ok, s_bitmap_fallback_attempted,
                                           s_bitmap_fallback_last_attempt, now_seconds)) {
        return live_ok;
    }
    // 直连失败：回退去取服务器每 5 分钟渲染一次的同款面板位图。
    ESP_LOGW(TAG, "%s", MARKET_PANEL_FALLBACK_LOG);
    s_bitmap_fallback_attempted = true;
    s_bitmap_fallback_last_attempt = now_seconds;
    return perform_aibrief_update();
}

void market_panel_note_window_unavailable()
{
    market_panel_record_failure();
}
