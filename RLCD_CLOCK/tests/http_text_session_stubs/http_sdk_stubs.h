// 为生产 HTTP 会话的状态回归声明最小 SDK 接口，不执行真实网络 I/O。
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

using esp_err_t = int;
using TickType_t = uint32_t;

inline constexpr esp_err_t ESP_OK = 0;
inline constexpr esp_err_t ESP_FAIL = -1;
inline constexpr esp_err_t ESP_ERR_NO_MEM = 0x101;
inline constexpr esp_err_t ESP_ERR_INVALID_ARG = 0x102;
inline constexpr esp_err_t ESP_ERR_INVALID_SIZE = 0x104;
inline constexpr esp_err_t ESP_ERR_TIMEOUT = 0x107;
inline constexpr esp_err_t ESP_ERR_HTTP_CONNECT = 0x7002;
inline constexpr esp_err_t ESP_ERR_HTTP_FETCH_HEADER = 0x7004;
inline constexpr esp_err_t ESP_ERR_HTTP_EAGAIN = 0x7007;
inline constexpr esp_err_t ESP_ERR_ESP_TLS_CONNECTION_TIMEOUT = 0x8001;

struct HostHttpClient;
using esp_http_client_handle_t = HostHttpClient *;
enum esp_http_client_event_id_t {
    HTTP_EVENT_ON_CONNECTED,
    HTTP_EVENT_ON_DATA,
    HTTP_EVENT_DISCONNECTED,
};

struct esp_http_client_event_t {
    esp_http_client_event_id_t event_id;
    void *user_data;
    void *data = nullptr;
    int data_len = 0;
};

struct esp_http_client_config_t {
    const char *url = nullptr;
    esp_err_t (*event_handler)(esp_http_client_event_t *) = nullptr;
    void *user_data = nullptr;
    int timeout_ms = 0;
    const char *cert_pem = nullptr;
    esp_err_t (*crt_bundle_attach)(void *) = nullptr;
};

esp_http_client_handle_t esp_http_client_init(const esp_http_client_config_t *config);
esp_err_t esp_http_client_cleanup(esp_http_client_handle_t client);
esp_err_t esp_http_client_set_url(esp_http_client_handle_t client, const char *url);
esp_err_t esp_http_client_set_user_data(esp_http_client_handle_t client, void *data);
esp_err_t esp_http_client_set_timeout_ms(esp_http_client_handle_t client, int timeout_ms);
esp_err_t esp_http_client_set_header(esp_http_client_handle_t client, const char *name, const char *value);
esp_err_t esp_http_client_perform(esp_http_client_handle_t client);
int esp_http_client_get_status_code(esp_http_client_handle_t client);
int64_t esp_http_client_get_content_length(esp_http_client_handle_t client);
esp_err_t esp_http_client_get_and_clear_last_tls_error(esp_http_client_handle_t client, int *code, int *flags);
esp_err_t esp_crt_bundle_attach(void *);
const char *esp_err_to_name(esp_err_t error);
int64_t esp_timer_get_time();
void vTaskDelay(TickType_t ticks);
size_t host_http_strlcpy(char *out, const char *text, size_t capacity);
size_t tinfl_decompress_mem_to_mem(void *, size_t, const void *, size_t, int);

inline constexpr size_t TINFL_DECOMPRESS_MEM_TO_MEM_FAILED = static_cast<size_t>(-1);
inline constexpr int TINFL_FLAG_USING_NON_WRAPPING_OUTPUT_BUF = 1;

template <typename... Args>
inline void host_http_log(Args...)
{
}

#define ESP_LOGI(...) host_http_log(__VA_ARGS__)
#define ESP_LOGW(...) host_http_log(__VA_ARGS__)
#define EXT_RAM_BSS_ATTR
#define pdMS_TO_TICKS(ms) static_cast<TickType_t>(ms)
#define strlcpy host_http_strlcpy
