// 定义服务器渲染位图（aibrief/latest.bin）的 1bpp 图形负载的固定尺寸和解析规则。
// 这条通道现在承载行情数据面板：服务器每 5 分钟渲染一张 400x300 的面板位图，
// 设备直连行情接口失败时用它兜底。标识符沿用 aibrief，因为下载地址与负载格式没有变。
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
// 服务器负载：4 字节魔数 + 小端宽度 + 小端高度 + N * 1bpp 位图（1 = 黑）。
inline constexpr size_t kEnvelopeBytes = 8;
inline constexpr size_t kPayloadBytes = kEnvelopeBytes + kBitmapBytes;
// 行情面板只有一页。负载格式本身仍允许多页（N * 15000 字节），但设备只缓存
// kMaxPages 页：页数超出的负载（例如旧版多页简报）整包拒收，不会只显示其中一页。
inline constexpr int kMaxPages = 1;
inline constexpr size_t kMaxBitmapBytes = kBitmapBytes * static_cast<size_t>(kMaxPages);
inline constexpr size_t kMaxPayloadBytes = kEnvelopeBytes + kMaxBitmapBytes;
inline constexpr uint8_t kMagic[4] = {'A', 'I', 'B', '1'};
inline constexpr int kMaxRefreshAttempts = 2;
inline constexpr uint32_t kRetrySettleMs = 150;

static_assert(kWidth % 8 == 0, "aibrief width must be byte aligned");
static_assert(kBytesPerRow * 8 == kWidth, "aibrief row stride must cover width");
static_assert(kBitmapBytes == 15000, "aibrief bitmap bytes must stay 15000");
static_assert(kPayloadBytes == 15008, "aibrief payload must stay 15008 bytes");
static_assert(kMaxPages > 0 && kMaxPages <= 255, "max pages must be 1..255 (uint8 page field)");
static_assert(kMaxRefreshAttempts > 0, "aibrief refresh must keep one attempt");
static_assert(kRetrySettleMs > 0, "aibrief retry settle delay must be positive");

enum class ParseResult {
    kOk,
    kShortPayload,
    kBadMagic,
    kBadSize,
    kNullArguments,
    kTooManyPages,
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

// 单页解析（兼容旧固件/测试）：要求恰好 15008 字节。
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
    // 兼容多页文件：若文件包含多页，单页接口只取第一页。
    memcpy(out_bitmap, data + kEnvelopeBytes, kBitmapBytes);
    return ParseResult::kOk;
}

// 多页解析：支持 1..kMaxPages 页，返回实际页数。
inline ParseResult parse_payload_multi(const uint8_t *data,
                                       size_t length,
                                       uint8_t *out_bitmaps,
                                       size_t out_capacity,
                                       size_t *out_page_count)
{
    if (!data || !out_bitmaps || !out_page_count) {
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
    const size_t bitmap_total = length - kEnvelopeBytes;
    if (bitmap_total % kBitmapBytes != 0) {
        return ParseResult::kBadSize;
    }
    const size_t pages = bitmap_total / kBitmapBytes;
    if (pages == 0) {
        return ParseResult::kShortPayload;
    }
    if (pages > static_cast<size_t>(kMaxPages)) {
        return ParseResult::kTooManyPages;
    }
    if (out_capacity < bitmap_total) {
        return ParseResult::kNullArguments;
    }
    memcpy(out_bitmaps, data + kEnvelopeBytes, bitmap_total);
    *out_page_count = pages;
    return ParseResult::kOk;
}

// 设备条件请求：服务器在 aibrief/version.txt 里放一行极简清单，
// 例如 "manifest=1 sha256=abcd... pages=1 bytes=15008 generated_at=..."。
// 设备先拉这几十字节，与上次成功发布的 sha256 相同就跳过整包下载；
// 清单声明的页数或字节数超出设备上限时也不下载。
inline constexpr size_t kManifestShaChars = 64;
inline constexpr size_t kManifestMaxBytes = 320;

struct ManifestInfo {
    bool ok = false;
    bool has_sha = false;
    // 清单本身合法，但声明的负载超出设备上限（页数或字节数）：不必下载。
    bool oversized = false;
    char sha256[kManifestShaChars + 1] = {};
    uint32_t pages = 0;
    uint32_t bytes = 0;
};

inline bool manifest_hex_ok(const char *text, size_t length)
{
    if (!text || length != kManifestShaChars) {
        return false;
    }
    for (size_t i = 0; i < length; ++i) {
        const char c = text[i];
        const bool hex = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') ||
                         (c >= 'A' && c <= 'F');
        if (!hex) {
            return false;
        }
    }
    return true;
}

inline uint32_t parse_decimal(const char *text, size_t length)
{
    uint32_t value = 0;
    for (size_t i = 0; i < length; ++i) {
        const char c = text[i];
        if (c < '0' || c > '9') {
            return 0;
        }
        value = value * 10U + static_cast<uint32_t>(c - '0');
    }
    return value;
}

// 解析清单文本；任何字段缺失/非法都只让该字段保持 0，不抛错。
inline ManifestInfo parse_manifest(const char *text, size_t length)
{
    ManifestInfo info;
    if (!text || length == 0 || length > kManifestMaxBytes) {
        return info;
    }
    size_t pos = 0;
    while (pos < length) {
        // 跳过空白与换行
        while (pos < length && (text[pos] == ' ' || text[pos] == '\n' ||
                                text[pos] == '\r' || text[pos] == '\t')) {
            ++pos;
        }
        const size_t key_start = pos;
        while (pos < length && text[pos] != '=' && text[pos] != ' ' &&
               text[pos] != '\n' && text[pos] != '\r' && text[pos] != '\t') {
            ++pos;
        }
        const size_t key_len = pos - key_start;
        if (pos >= length || text[pos] != '=') {
            continue;
        }
        ++pos; // 跳过 '='
        const size_t value_start = pos;
        while (pos < length && text[pos] != ' ' && text[pos] != '\n' &&
               text[pos] != '\r' && text[pos] != '\t') {
            ++pos;
        }
        const size_t value_len = pos - value_start;
        const char *key = text + key_start;
        const char *value = text + value_start;
        if (key_len == 6 && memcmp(key, "sha256", 6) == 0) {
            if (manifest_hex_ok(value, value_len)) {
                memcpy(info.sha256, value, value_len);
                info.sha256[value_len] = '\0';
                info.has_sha = true;
            }
        } else if (key_len == 5 && memcmp(key, "pages", 5) == 0) {
            info.pages = parse_decimal(value, value_len);
        } else if (key_len == 5 && memcmp(key, "bytes", 5) == 0) {
            info.bytes = parse_decimal(value, value_len);
        }
    }
    info.ok = info.has_sha;
    if (info.ok && (info.pages > static_cast<uint32_t>(kMaxPages) ||
                    info.bytes > kMaxPayloadBytes)) {
        info.ok = false;
        info.oversized = true;
    }
    return info;
}

} // namespace aibrief
