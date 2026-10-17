// 声明配网门户和 OTA 主备来源的应用级编译期网络配置。
#pragma once

inline constexpr const char *kSetupApPassword = "12345678";
inline constexpr const char *kSetupPortalIp = "192.168.4.1";
inline constexpr const char *kSetupPortalUrl = "http://192.168.4.1/";

#ifndef WEATHER_CLOCK_OTA_MANIFEST_URL
#if __has_include("ota_endpoint_local.h")
#include "ota_endpoint_local.h"
#else
#define WEATHER_CLOCK_OTA_MANIFEST_URL "https://example.invalid/firmware/latest.json"
#endif
#endif
inline constexpr const char *kOtaManifestUrl = WEATHER_CLOCK_OTA_MANIFEST_URL;

#ifndef WEATHER_CLOCK_OTA_BACKUP_MANIFEST_URL
#define WEATHER_CLOCK_OTA_BACKUP_MANIFEST_URL "https://example.invalid/firmware/latest.json"
#endif
inline constexpr const char *kOtaBackupManifestUrl = WEATHER_CLOCK_OTA_BACKUP_MANIFEST_URL;

// 章鱼 AI 简报图形负载：公开仓库文件，每天由工作流重建，无需密钥。
// 主源走 raw.githubusercontent，备用源走 jsDelivr 镜像。
#ifndef WEATHER_CLOCK_AIBRIEF_URL
#define WEATHER_CLOCK_AIBRIEF_URL \
    "https://raw.githubusercontent.com/k-macao/04-rlcd/main/aibrief/latest.bin"
#endif
#ifndef WEATHER_CLOCK_AIBRIEF_BACKUP_URL
#define WEATHER_CLOCK_AIBRIEF_BACKUP_URL \
    "https://cdn.jsdelivr.net/gh/k-macao/04-rlcd@main/aibrief/latest.bin"
#endif
inline constexpr const char *kAibriefUrl = WEATHER_CLOCK_AIBRIEF_URL;
inline constexpr const char *kAibriefBackupUrl = WEATHER_CLOCK_AIBRIEF_BACKUP_URL;
