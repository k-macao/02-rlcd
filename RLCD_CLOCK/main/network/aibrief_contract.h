// 定义章鱼 AI 简报页 1bpp 图形负载的固定尺寸和解析规则。
// v1.8+ 支持多页：单文件包含 1..kMaxPages 张 400x300 位图，设备每 30 分钟自动翻页。
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
// 多页支持：最多 255 页（3,825,000 字节位图 ≈ 3.65MB），完整展示 002 仓库全量内容。
// 255 是协议硬上限：页数字段在设备侧用 uint8_t 传递（见 AibriefSnapshot::page_count）。
inline constexpr int kMaxPages = 255;
inline constexpr size_t kMaxBitmapBytes = kBitmapBytes * static_cast<size_t>(kMaxPages);
inline constexpr size_t kMaxPayloadBytes = kEnvelopeBytes + kMaxBitmapBytes;
inline constexpr size_t kDownloadBufferBytes = kMaxPayloadBytes + 1;
inline constexpr uint8_t kMagic[4] = {'A', 'I', 'B', '1'};
// 设备端简报页每 5 分钟拉取一次已推送的页面（开机即联网获取）。
inline constexpr uint32_t kMaxRefreshAgeSeconds = 5 * 60;
// 分页自动轮播间隔：每页停留 30 分钟（1800 秒）。
// 换页频率与数据更新解耦：内容由清单比对触发刷新，翻页只负责慢慢把整份简报读完。
inline constexpr uint32_t kPageRotateSeconds = 30 * 60;
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
// 例如 "manifest=1 sha256=abcd... pages=104 bytes=1560008 generated_at=..."。
// 设备先拉这几十字节，与上次成功发布的 sha256 相同就跳过整包下载，
// 避免每 5 分钟重下 3.8MB，也不会把轮播重置回第 1 页。
inline constexpr size_t kManifestShaChars = 64;
inline constexpr size_t kManifestMaxBytes = 320;

struct ManifestInfo {
    bool ok = false;
    bool has_sha = false;
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
    if (info.ok && info.pages > static_cast<uint32_t>(kMaxPages)) {
        info.ok = false;
    }
    return info;
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
