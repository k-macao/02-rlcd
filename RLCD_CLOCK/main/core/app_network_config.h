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

// 章鱼 AI 简报图形负载：公开仓库文件，每 3 小时由工作流重建，无需密钥。
// 主源走 raw.githubusercontent，备用源走 jsDelivr 镜像。
// 清单 version.txt 只有几十字节：设备先比对 sha256，内容没变就不下载整包。
#ifndef WEATHER_CLOCK_AIBRIEF_URL
#define WEATHER_CLOCK_AIBRIEF_URL \
    "https://raw.githubusercontent.com/k-macao/02-rlcd/main/aibrief/latest.bin"
#endif
#ifndef WEATHER_CLOCK_AIBRIEF_BACKUP_URL
#define WEATHER_CLOCK_AIBRIEF_BACKUP_URL \
    "https://cdn.jsdelivr.net/gh/k-macao/02-rlcd@main/aibrief/latest.bin"
#endif
#ifndef WEATHER_CLOCK_AIBRIEF_MANIFEST_URL
#define WEATHER_CLOCK_AIBRIEF_MANIFEST_URL \
    "https://raw.githubusercontent.com/k-macao/02-rlcd/main/aibrief/version.txt"
#endif
#ifndef WEATHER_CLOCK_AIBRIEF_MANIFEST_BACKUP_URL
#define WEATHER_CLOCK_AIBRIEF_MANIFEST_BACKUP_URL \
    "https://cdn.jsdelivr.net/gh/k-macao/02-rlcd@main/aibrief/version.txt"
#endif
inline constexpr const char *kAibriefUrl = WEATHER_CLOCK_AIBRIEF_URL;
inline constexpr const char *kAibriefBackupUrl = WEATHER_CLOCK_AIBRIEF_BACKUP_URL;
inline constexpr const char *kAibriefManifestUrl = WEATHER_CLOCK_AIBRIEF_MANIFEST_URL;
inline constexpr const char *kAibriefManifestBackupUrl =
    WEATHER_CLOCK_AIBRIEF_MANIFEST_BACKUP_URL;
