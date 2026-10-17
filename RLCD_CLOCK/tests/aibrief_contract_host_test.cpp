// 校验章鱼 AI 简报负载解析与刷新窗口的纯逻辑。
#include "aibrief_contract.h"

#include <assert.h>
#include <string.h>

#include <vector>

namespace {

std::vector<uint8_t> make_payload(uint16_t width, uint16_t height)
{
    std::vector<uint8_t> payload(aibrief::kPayloadBytes, 0x5A);
    memcpy(payload.data(), aibrief::kMagic, sizeof(aibrief::kMagic));
    payload[4] = static_cast<uint8_t>(width & 0xFF);
    payload[5] = static_cast<uint8_t>((width >> 8) & 0xFF);
    payload[6] = static_cast<uint8_t>(height & 0xFF);
    payload[7] = static_cast<uint8_t>((height >> 8) & 0xFF);
    return payload;
}

void test_parses_valid_payload()
{
    std::vector<uint8_t> payload = make_payload(aibrief::kWidth, aibrief::kHeight);
    uint8_t bitmap[aibrief::kBitmapBytes] = {};
    assert(aibrief::parse_payload(payload.data(), payload.size(), bitmap) ==
           aibrief::ParseResult::kOk);
    assert(bitmap[0] == payload[aibrief::kEnvelopeBytes]);
    assert(bitmap[aibrief::kBitmapBytes - 1] ==
           payload[aibrief::kPayloadBytes - 1]);
}

void test_rejects_bad_inputs()
{
    uint8_t bitmap[aibrief::kBitmapBytes] = {};
    std::vector<uint8_t> payload = make_payload(aibrief::kWidth, aibrief::kHeight);
    assert(aibrief::parse_payload(payload.data(), 10, bitmap) ==
           aibrief::ParseResult::kShortPayload);
    assert(aibrief::parse_payload(nullptr, payload.size(), bitmap) ==
           aibrief::ParseResult::kNullArguments);
    assert(aibrief::parse_payload(payload.data(), payload.size(), nullptr) ==
           aibrief::ParseResult::kNullArguments);
    payload[0] = 'X';
    assert(aibrief::parse_payload(payload.data(), payload.size(), bitmap) ==
           aibrief::ParseResult::kBadMagic);
    payload = make_payload(320, aibrief::kHeight);
    assert(aibrief::parse_payload(payload.data(), payload.size(), bitmap) ==
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

} // namespace

int main()
{
    test_parses_valid_payload();
    test_rejects_bad_inputs();
    test_refresh_window();
    test_bit_helpers();
    return 0;
}
