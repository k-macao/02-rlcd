// 声明网络与音频电源管理锁的初始化、状态和嵌套所有权接口。
#pragma once

struct PowerLockDepthSnapshot {
    int network = 0;
    int audio = 0;
    int audio_wake = 0;
    int audio_cpu = 0;
};

[[nodiscard]] bool acquire_network_awake_lock();
void release_network_awake_lock();
bool network_awake_lock_active();
bool get_power_lock_depth_snapshot(PowerLockDepthSnapshot *out);
[[nodiscard]] bool acquire_audio_awake_lock();
void release_audio_awake_lock();
