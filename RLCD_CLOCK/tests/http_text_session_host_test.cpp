// 回归生产 HTTP 会话在传输失败后的状态隔离、复用和资源所有权。
#include "app_metadata.h"
#include "network_http_client.h"
#include "network_http_transaction_lock.h"
#include "runtime_health.h"

#include <cassert>
#include <cstring>
#include <deque>
#include <initializer_list>
#include <string>
#include <vector>

const char *const TAG = "HostHttpSession";

namespace {
constexpr const char *kNow = "https://unit.qweatherapi.com/now";
constexpr const char *kAlert = "https://unit.qweatherapi.com/alert";
constexpr const char *kDaily = "https://unit.qweatherapi.com/daily";
constexpr const char *kAir = "https://unit.qweatherapi.com/air";

struct Reply {
    const char *url;
    esp_err_t error = ESP_OK;
    const char *body = "{}";
    int status = 200;
    int tls_flags = 0;
};

struct Model {
    std::deque<Reply> replies;
    std::vector<std::string> sent_urls;
    std::vector<int> timeouts;
    std::vector<bool> legacy_ca;
    int created = 0;
    int cleaned = 0;
    int live = 0;
    int continued_old_request = 0;
    int locks = 0;
    int acquires = 0;
    int releases = 0;
    int http_failures = 0;
    int budget_ms = 60000;
    int64_t now_us = 1000000;
    std::vector<TickType_t> delays;
} model;

void reset(std::initializer_list<Reply> replies)
{
    assert(model.live == 0 && model.locks == 0);
    model = {};
    model.replies = replies;
}

void verify_finished()
{
    assert(model.replies.empty());
    assert(model.live == 0 && model.locks == 0);
    assert(model.created == model.cleaned);
    assert(model.acquires == model.releases);
    assert(model.continued_old_request == 0);
}
} // namespace

struct HostHttpClient {
    std::string url;
    std::string delayed_body;
    esp_err_t (*handler)(esp_http_client_event_t *) = nullptr;
    void *user_data = nullptr;
    int timeout_ms = 0;
    int status = -1;
    int tls_flags = 0;
    int64_t content_length = 0;
    bool connected = false;
    bool awaiting_headers = false;
    bool legacy_ca = false;
};

namespace {
void dispatch(HostHttpClient *client,
              esp_http_client_event_id_t id,
              const std::string &body = {})
{
    esp_http_client_event_t event{id, client->user_data,
                                 const_cast<char *>(body.data()),
                                 static_cast<int>(body.size())};
    assert(client->handler(&event) == ESP_OK);
}
} // namespace

esp_http_client_handle_t esp_http_client_init(const esp_http_client_config_t *config)
{
    assert(config && model.locks == 1);
    auto *client = new HostHttpClient;
    client->url = config->url;
    client->handler = config->event_handler;
    client->user_data = config->user_data;
    client->timeout_ms = config->timeout_ms;
    client->legacy_ca = config->cert_pem != nullptr;
    ++model.created;
    ++model.live;
    return client;
}

esp_err_t esp_http_client_cleanup(esp_http_client_handle_t client)
{
    assert(client && model.locks == 1);
    dispatch(client, HTTP_EVENT_DISCONNECTED);
    delete client;
    ++model.cleaned;
    --model.live;
    model.now_us += 25000;
    return ESP_OK;
}

esp_err_t esp_http_client_set_url(esp_http_client_handle_t client, const char *url)
{
    client->url = url;
    // ESP-IDF 5.5.3 keeps a pending request state on same-origin set_url().
    return ESP_OK;
}

esp_err_t esp_http_client_set_user_data(esp_http_client_handle_t client, void *data)
{
    client->user_data = data;
    return ESP_OK;
}

esp_err_t esp_http_client_set_timeout_ms(esp_http_client_handle_t client, int timeout_ms)
{
    client->timeout_ms = timeout_ms;
    return ESP_OK;
}

esp_err_t esp_http_client_set_header(esp_http_client_handle_t client,
                                    const char *name, const char *value)
{
    assert(client && name && value && model.locks == 1);
    return ESP_OK;
}

esp_err_t esp_http_client_perform(esp_http_client_handle_t client)
{
    assert(model.locks == 1);
    if (client->awaiting_headers) {
        // Resume the timed-out response without sending the new endpoint.
        ++model.continued_old_request;
        client->status = 200;
        client->content_length = client->delayed_body.size();
        dispatch(client, HTTP_EVENT_ON_DATA, client->delayed_body);
        client->awaiting_headers = false;
        return ESP_OK;
    }
    assert(!model.replies.empty());
    const Reply reply = model.replies.front();
    model.replies.pop_front();
    assert(client->url == reply.url);
    model.sent_urls.push_back(client->url);
    model.timeouts.push_back(client->timeout_ms);
    model.legacy_ca.push_back(client->legacy_ca);
    client->tls_flags = reply.tls_flags;
    client->status = reply.error == ESP_OK ? reply.status : -1;
    client->content_length = std::strlen(reply.body);
    if (!client->connected && reply.error != ESP_ERR_HTTP_CONNECT) {
        model.now_us += 1000;
        client->connected = true;
        dispatch(client, HTTP_EVENT_ON_CONNECTED);
    }
    if (reply.error != ESP_OK) {
        model.now_us += static_cast<int64_t>(client->timeout_ms) * 1000;
        client->awaiting_headers = reply.error == ESP_ERR_HTTP_EAGAIN ||
                                   reply.error == ESP_ERR_HTTP_FETCH_HEADER;
        client->delayed_body = reply.body;
        return reply.error;
    }
    model.now_us += 100000;
    dispatch(client, HTTP_EVENT_ON_DATA, reply.body);
    return ESP_OK;
}

int esp_http_client_get_status_code(esp_http_client_handle_t client)
{
    return client->status;
}

int64_t esp_http_client_get_content_length(esp_http_client_handle_t client)
{
    return client->content_length;
}

esp_err_t esp_http_client_get_and_clear_last_tls_error(
    esp_http_client_handle_t client, int *code, int *flags)
{
    *code = 0;
    *flags = client->tls_flags;
    client->tls_flags = 0;
    return ESP_OK;
}

esp_err_t esp_crt_bundle_attach(void *) { return ESP_OK; }
const char *esp_err_to_name(esp_err_t) { return "host-error"; }
int64_t esp_timer_get_time() { return model.now_us; }
int boot_sync_remaining_ms() { return model.budget_ms; }
void vTaskDelay(TickType_t ticks)
{
    model.delays.push_back(ticks);
    model.now_us += static_cast<int64_t>(ticks) * 1000;
}

bool acquire_network_http_transaction_lock(TickType_t)
{
    assert(model.locks == 0);
    ++model.locks;
    ++model.acquires;
    return true;
}

void release_network_http_transaction_lock()
{
    assert(model.locks == 1 && model.live == 0);
    --model.locks;
    ++model.releases;
}

void runtime_health_note_event(RuntimeHealthEvent event)
{
    assert(event == RuntimeHealthEvent::kHttpFailure);
    ++model.http_failures;
}

size_t host_http_strlcpy(char *out, const char *text, size_t capacity)
{
    const size_t length = std::strlen(text);
    if (capacity > 0) {
        const size_t copied = length < capacity ? length : capacity - 1;
        std::memcpy(out, text, copied);
        out[copied] = '\0';
    }
    return length;
}

size_t tinfl_decompress_mem_to_mem(void *, size_t, const void *, size_t, int)
{
    return TINFL_DECOMPRESS_MEM_TO_MEM_FAILED;
}

namespace {
void test_failed_endpoint_is_not_resumed(esp_err_t error,
                                          const char *failed,
                                          const char *next)
{
    reset({{kNow}, {failed, error, "late-old-response"}, {next, ESP_OK, "new-response"}});
    {
        HttpTextSession session;
        char body[64] = {};
        assert(session.get(kNow, body, sizeof(body)) == ESP_OK);
        assert(session.get(failed, body, sizeof(body), nullptr, "alert") == error);
        const HttpTextRequestStats failed_stats = session.last_request_stats();
        assert(failed_stats.result == error && failed_stats.attempt_count == 1);
        assert(failed_stats.reused_client && failed_stats.elapsed_ms == 10000);
        assert(session.get(next, body, sizeof(body)) == ESP_OK);
        assert(std::strcmp(body, "new-response") == 0);
        assert(model.sent_urls == std::vector<std::string>({kNow, failed, next}));
        assert(model.created == 2 && model.cleaned == 1 && model.locks == 1);
        assert(session.stats().transient_retry_count == 0);
    }
    verify_finished();
}

void test_success_and_complete_http_error_remain_reusable()
{
    reset({{kNow}, {kAlert, ESP_OK, "denied", 403}, {kDaily}, {kAir}});
    {
        HttpTextSession session;
        char body[64] = {};
        assert(session.get(kNow, body, sizeof(body)) == ESP_OK);
        assert(session.get(kAlert, body, sizeof(body)) == ESP_FAIL);
        assert(session.get(kDaily, body, sizeof(body)) == ESP_OK);
        assert(session.get(kAir, body, sizeof(body)) == ESP_OK);
        const HttpTextSessionStats stats = session.stats();
        assert(stats.client_create_count == 1 && stats.connection_count == 1);
        assert(stats.reused_request_count == 3 && model.cleaned == 0);
    }
    verify_finished();
}

void test_last_failure_and_cancel_release_resources()
{
    for (bool cancel : {false, true}) {
        reset({{kNow}, {kAir, ESP_ERR_HTTP_EAGAIN}});
        {
            HttpTextSession session;
            char body[64] = {};
            assert(session.get(kNow, body, sizeof(body)) == ESP_OK);
            assert(session.get(kAir, body, sizeof(body)) == ESP_ERR_HTTP_EAGAIN);
            assert(model.live == 0 && model.cleaned == 1 && model.locks == 1);
            if (cancel) {
                model.budget_ms = 0;
                assert(session.get(kDaily, body, sizeof(body)) == ESP_ERR_TIMEOUT);
                assert(model.sent_urls.size() == 2 && model.created == 1);
            }
        }
        verify_finished();
    }
}

void test_transient_retry_stays_bounded()
{
    reset({{kNow, ESP_ERR_HTTP_EAGAIN}, {kNow, ESP_ERR_HTTP_EAGAIN}});
    {
        HttpTextSession session;
        char body[64] = {};
        assert(session.get(kNow, body, sizeof(body)) == ESP_ERR_HTTP_EAGAIN);
        assert(session.last_request_stats().attempt_count == 2);
        assert(session.stats().transient_retry_count == 1);
        assert(model.timeouts == std::vector<int>({10000, 3000}));
        assert(model.delays == std::vector<TickType_t>({200}));
        assert(model.live == 0 && model.locks == 1);
    }
    verify_finished();
}

void test_legacy_ca_and_one_shot_ownership()
{
    reset({{kNow, ESP_ERR_HTTP_CONNECT, "", 200, 1}, {kNow}, {kAir}});
    {
        HttpTextSession session;
        char body[64] = {};
        assert(session.get(kNow, body, sizeof(body)) == ESP_OK);
        assert(session.get(kAir, body, sizeof(body)) == ESP_OK);
        assert(model.legacy_ca == std::vector<bool>({false, true, true}));
        assert(model.delays == std::vector<TickType_t>({350}));
    }
    verify_finished();

    constexpr const char *url = "https://example.test/fail";
    reset({{url, ESP_ERR_HTTP_EAGAIN}});
    {
        HttpTextSession session(false);
        char body[64] = {};
        assert(session.get(url, body, sizeof(body)) == ESP_ERR_HTTP_EAGAIN);
        assert(model.live == 0 && model.locks == 0 && model.delays.empty());
    }
    verify_finished();
}
} // namespace

int main()
{
    for (esp_err_t error : {ESP_ERR_HTTP_EAGAIN, ESP_ERR_HTTP_FETCH_HEADER}) {
        test_failed_endpoint_is_not_resumed(error, kAlert, kDaily);
        test_failed_endpoint_is_not_resumed(error, kDaily, kAir);
    }
    test_success_and_complete_http_error_remain_reusable();
    test_last_failure_and_cancel_release_resources();
    test_transient_retry_stays_bounded();
    test_legacy_ca_and_one_shot_ownership();
    return 0;
}
