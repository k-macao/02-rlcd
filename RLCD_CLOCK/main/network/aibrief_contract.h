// 定义章鱼 AI 简报页 1bpp 图形负载的固定尺寸和解析规则。
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace aibrief {

inline constexpr int kWidth = 400;
inline constexpr int kHeight = 300;
inline constexpr int kBytesPerRow = kWidth / 8;
inline constexpr size_t kBitmapBytes =
    static_cast<size_t>(kBytesPerRow) * static_cast<size_t>(kHeight);
// 服务器负载：4 字节魔数 + 小端宽度 + 小端高度 + 1bpp 位图（1 = 黑）。
inline constexpr size_t kEnvelopeBytes = 8;
inline constexpr size_t kPayloadBytes = kEnvelopeBytes + kBitmapBytes;
inline constexpr size_t kDownloadBufferBytes = kPayloadBytes + 1;
inline constexpr uint8_t kMagic[4] = {'A', 'I', 'B', '1'};
inline constexpr uint32_t kMaxRefreshAgeSeconds = 6 * 60 * 60;
inline constexpr int kMaxRefreshAttempts = 2;
inline constexpr uint32_t kRetrySettleMs = 150;

static_assert(kWidth % 8 == 0, "aibrief width must be byte aligned");
static_assert(kBytesPerRow * 8 == kWidth, "aibrief row stride must cover width");
static_assert(kBitmapBytes == 15000, "aibrief bitmap bytes must stay 15000");
static_assert(kPayloadBytes == 15008, "aibrief payload must stay 15008 bytes");
static_assert(kMaxRefreshAttempts > 0, "aibrief refresh must keep one attempt");
static_assert(kRetrySettleMs > 0, "aibrief retry settle delay must be positive");

enum class ParseResult {
    kOk,
    kShortPayload,
    kBadMagic,
    kBadSize,
    kNullArguments,
};

inline uint16_t read_u16_le(const uint8_t *data)
{
    return static_cast<uint16_t>(data[0]) |
           static_cast<uint16_t>(data[1] << 8);
}

constexpr bool bit_is_set(const uint8_t *bits, uint32_t bit_index)
{
    return bits && (bits[bit_index / 8] & (0x80U >> (bit_index % 8))) != 0;
}

inline ParseResult parse_payload(const uint8_t *data,
                                 size_t length,
                                 uint8_t *out_bitmap)
{
    if (!data || !out_bitmap) {
        return ParseResult::kNullArguments;
    }
    if (length < kPayloadBytes) {
        return ParseResult::kShortPayload;
    }
    if (memcmp(data, kMagic, sizeof(kMagic)) != 0) {
        return ParseResult::kBadMagic;
    }
    if (read_u16_le(data + 4) != kWidth || read_u16_le(data + 6) != kHeight) {
        return ParseResult::kBadSize;
    }
    memcpy(out_bitmap, data + kEnvelopeBytes, kBitmapBytes);
    return ParseResult::kOk;
}

constexpr bool refresh_due(uint32_t last_sync_seconds,
                           uint32_t now_seconds,
                           bool available)
{
    return !available ||
           last_sync_seconds == 0 ||
           now_seconds < last_sync_seconds ||
           now_seconds - last_sync_seconds >= kMaxRefreshAgeSeconds;
}

} // namespace aibrief
