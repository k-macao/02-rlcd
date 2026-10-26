// 定义应用日志标签、版本号和构建日期等只读元数据。
#include "app_metadata.h"

const char *const TAG = "WeatherClock";
// 版本号由 CMake 从 PROJECT_VER 注入，杜绝与工程版本再次漂移：
// 历史上这里硬编码过 "v1.6.17"，而 PROJECT_VER 已经走到 v1.8.x，
// 导致设备自报旧版本、OTA 每次检查都判定「有新版本」并反复下载。
#ifndef WEATHER_CLOCK_APP_VERSION
#error "WEATHER_CLOCK_APP_VERSION must be provided by main/CMakeLists.txt from PROJECT_VER"
#endif
const char *const APP_VERSION = WEATHER_CLOCK_APP_VERSION;
#ifndef WEATHER_CLOCK_BUILD_DATE
#define WEATHER_CLOCK_BUILD_DATE "unknown"
#endif
const char *const APP_BUILD_DATE = WEATHER_CLOCK_BUILD_DATE;
