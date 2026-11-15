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

// 行情数据面板：设备每分钟直连下面四个公开行情接口（无需密钥），在本机画出面板。
// 地址必须与服务器渲染器 device_brief/market_quotes.py 里的常量逐字一致，
// device_brief/test_render_brief.py 会比对两边。
#ifndef WEATHER_CLOCK_MARKET_TENCENT_URL
#define WEATHER_CLOCK_MARKET_TENCENT_URL \
    "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006,hkHSI,hkHSTECH," \
    "usDJI,usIXIC,usINX,gzN225,gzKS11,gzKOSDAQ"
#endif
#ifndef WEATHER_CLOCK_MARKET_SINA_HK_URL
#define WEATHER_CLOCK_MARKET_SINA_HK_URL "https://hq.sinajs.cn/list=rt_hkHSI,rt_hkHSTECH"
#endif
#ifndef WEATHER_CLOCK_MARKET_SINA_REFERER
#define WEATHER_CLOCK_MARKET_SINA_REFERER "https://finance.sina.com.cn/"
#endif
#ifndef WEATHER_CLOCK_MARKET_NAVER_KR_URL
#define WEATHER_CLOCK_MARKET_NAVER_KR_URL \
    "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI,KOSDAQ"
#endif
#ifndef WEATHER_CLOCK_MARKET_NAVER_JP_URL
#define WEATHER_CLOCK_MARKET_NAVER_JP_URL \
    "https://polling.finance.naver.com/api/realtime/worldstock/index/.N225,.TOPX"
#endif
inline constexpr const char *kMarketTencentUrl = WEATHER_CLOCK_MARKET_TENCENT_URL;
inline constexpr const char *kMarketSinaHkUrl = WEATHER_CLOCK_MARKET_SINA_HK_URL;
inline constexpr const char *kMarketSinaReferer = WEATHER_CLOCK_MARKET_SINA_REFERER;
inline constexpr const char *kMarketNaverKrUrl = WEATHER_CLOCK_MARKET_NAVER_KR_URL;
inline constexpr const char *kMarketNaverJpUrl = WEATHER_CLOCK_MARKET_NAVER_JP_URL;

// 行情面板的位图兜底：服务器每 5 分钟用同样的数据源渲染一张同款面板（公开仓库文件，
// 无需密钥），设备直连行情接口失败时才下载。旧版固件只认这份位图。
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
