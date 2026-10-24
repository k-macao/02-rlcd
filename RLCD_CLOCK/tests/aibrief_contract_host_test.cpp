// 校验章鱼 AI 简报负载解析与刷新窗口的纯逻辑（支持多页）。
#include "aibrief_contract.h"
#include "aibrief_state.h"
#include "ui_aibrief_page_format.h"

#include <assert.h>
#include <string.h>

#include <vector>

namespace {

std::vector<uint8_t> make_payload(uint16_t width, uint16_t height, size_t pages = 1)
{
    const size_t total = aibrief::kEnvelopeBytes + aibrief::kBitmapBytes * pages;
    std::vector<uint8_t> payload(total, 0x5A);
    memcpy(payload.data(), aibrief::kMagic, sizeof(aibrief::kMagic));
    payload[4] = static_cast<uint8_t>(width & 0xFF);
    payload[5] = static_cast<uint8_t>((width >> 8) & 0xFF);
    payload[6] = static_cast<uint8_t>(height & 0xFF);
    payload[7] = static_cast<uint8_t>((height >> 8) & 0xFF);
    // 每页填充不同值，便于校验
    for (size_t p = 0; p < pages; ++p) {
        payload[aibrief::kEnvelopeBytes + p * aibrief::kBitmapBytes] = static_cast<uint8_t>(p);
    }
    return payload;
}

void test_parses_valid_payload()
{
    std::vector<uint8_t> payload = make_payload(aibrief::kWidth, aibrief::kHeight, 1);
    uint8_t bitmap[aibrief::kBitmapBytes] = {};
    assert(aibrief::parse_payload(payload.data(), payload.size(), bitmap) ==
           aibrief::ParseResult::kOk);
    assert(bitmap[0] == payload[aibrief::kEnvelopeBytes]);
    assert(bitmap[aibrief::kBitmapBytes - 1] ==
           payload[aibrief::kEnvelopeBytes + aibrief::kBitmapBytes - 1]);

    // 多页解析
    const size_t pages = 3;
    payload = make_payload(aibrief::kWidth, aibrief::kHeight, pages);
    std::vector<uint8_t> bitmaps(aibrief::kBitmapBytes * pages, 0);
    size_t out_pages = 0;
    assert(aibrief::parse_payload_multi(payload.data(), payload.size(), bitmaps.data(),
                                        bitmaps.size(), &out_pages) ==
           aibrief::ParseResult::kOk);
    assert(out_pages == pages);
    assert(bitmaps[0] == 0);
    assert(bitmaps[aibrief::kBitmapBytes] == 1);
    assert(bitmaps[aibrief::kBitmapBytes * 2] == 2);

    // 48 页上限
    payload = make_payload(aibrief::kWidth, aibrief::kHeight, aibrief::kMaxPages);
    bitmaps.assign(aibrief::kBitmapBytes * aibrief::kMaxPages, 0);
    out_pages = 0;
    assert(aibrief::parse_payload_multi(payload.data(), payload.size(), bitmaps.data(),
                                        bitmaps.size(), &out_pages) ==
           aibrief::ParseResult::kOk);
    assert(out_pages == static_cast<size_t>(aibrief::kMaxPages));
}

void test_rejects_bad_inputs()
{
    uint8_t bitmap[aibrief::kBitmapBytes] = {};
    std::vector<uint8_t> payload = make_payload(aibrief::kWidth, aibrief::kHeight, 1);
    assert(aibrief::parse_payload(payload.data(), 10, bitmap) ==
           aibrief::ParseResult::kShortPayload);
    assert(aibrief::parse_payload(nullptr, payload.size(), bitmap) ==
           aibrief::ParseResult::kNullArguments);
    assert(aibrief::parse_payload(payload.data(), payload.size(), nullptr) ==
           aibrief::ParseResult::kNullArguments);
    payload[0] = 'X';
    assert(aibrief::parse_payload(payload.data(), payload.size(), bitmap) ==
           aibrief::ParseResult::kBadMagic);
    payload = make_payload(320, aibrief::kHeight, 1);
    assert(aibrief::parse_payload(payload.data(), payload.size(), bitmap) ==
           aibrief::ParseResult::kBadSize);

    // 多页：过多页
    payload = make_payload(aibrief::kWidth, aibrief::kHeight, aibrief::kMaxPages + 1);
    std::vector<uint8_t> bitmaps(aibrief::kBitmapBytes * (aibrief::kMaxPages + 1), 0);
    size_t out_pages = 0;
    assert(aibrief::parse_payload_multi(payload.data(), payload.size(), bitmaps.data(),
                                        bitmaps.size(), &out_pages) ==
           aibrief::ParseResult::kTooManyPages);

    // 多页：非对齐
    payload = make_payload(aibrief::kWidth, aibrief::kHeight, 1);
    payload.push_back(0x00); // 破坏对齐
    bitmaps.assign(aibrief::kBitmapBytes * 2, 0);
    assert(aibrief::parse_payload_multi(payload.data(), payload.size(), bitmaps.data(),
                                        bitmaps.size(), &out_pages) ==
           aibrief::ParseResult::kBadSize);
}

void test_refresh_window()
{
    assert(aibrief::refresh_due(0, 1000, false));
    assert(aibrief::refresh_due(0, 1000, true));
    assert(!aibrief::refresh_due(100, 200, true));
    assert(!aibrief::refresh_due(1000, 1000, true));
    assert(aibrief::refresh_due(1, 1 + aibrief::kMaxRefreshAgeSeconds, true));
    assert(aibrief::refresh_due(1000, 999, true));
}

void test_bit_helpers()
{
    const uint8_t bits[2] = {0x80, 0x01};
    assert(aibrief::bit_is_set(bits, 0));
    assert(!aibrief::bit_is_set(bits, 1));
    assert(aibrief::bit_is_set(bits, 15));
    assert(!aibrief::bit_is_set(nullptr, 0));
}

void test_read_time_and_page_indicator()
{
    char text[48] = {};
    struct tm local = {};
    local.tm_hour = 7;
    local.tm_min = 9;
    format_aibrief_read_indicator(text, sizeof(text), &local, 1, 28);
    assert(strcmp(text, "读取 07:09 · 01 / 28") == 0);
    AibriefReadIndicatorParts parts;
    format_aibrief_read_indicator_parts(&local, 1, 28, parts);
    assert(strcmp(parts.prefix, "读取 ") == 0);
    assert(strcmp(parts.read_time, "07:09") == 0);
    assert(strcmp(parts.separator, " · ") == 0);
    assert(strcmp(parts.current_page, "01") == 0);
    assert(strcmp(parts.page_separator, " / ") == 0);
    assert(strcmp(parts.page_count, "28") == 0);
    format_aibrief_read_indicator(text, sizeof(text), &local, 1, 1);
    assert(strcmp(text, "读取 07:09 · 01 / 01") == 0);
    format_aibrief_read_indicator(text, sizeof(text), nullptr, 1, 28);
    assert(strcmp(text, "01 / 28") == 0);

    // 两位字段必须始终保持固定长度；异常输入饱和到显示上限。
    local.tm_hour = -1;
    local.tm_min = 90;
    format_aibrief_read_indicator(text, sizeof(text), &local, 100, 255);
    assert(strcmp(text, "读取 00:59 · 99 / 99") == 0);
    format_aibrief_read_indicator(nullptr, 0, &local, 1, 28);
}

void test_failed_sync_keeps_last_successful_read_time()
{
    AibriefSnapshot snapshot = {};
    aibrief_record_sync_time(snapshot, 100, true);
    assert(snapshot.last_sync == 100);
    assert(snapshot.last_success == 100);
    aibrief_record_sync_time(snapshot, 200, false);
    assert(snapshot.last_sync == 200);
    assert(snapshot.last_success == 100);
    aibrief_record_sync_time(snapshot, 300, true);
    assert(snapshot.last_sync == 300);
    assert(snapshot.last_success == 300);
}

} // namespace

int main()
{
    test_parses_valid_payload();
    test_rejects_bad_inputs();
    test_refresh_window();
    test_bit_helpers();
    test_read_time_and_page_indicator();
    test_failed_sync_keeps_last_successful_read_time();
    return 0;
}
