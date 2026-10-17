// 验证联网任务 NTP 运行态的启动、失败退避、成功和每日零点转换。
#include "network_ntp_schedule_state.h"
#include "network_sync_schedule.h"
#include "sensor_time.h"

#include <assert.h>
#include <stdlib.h>
#include <time.h>

namespace {
constexpr time_t kPlausibleNow = 1783890000;
constexpr time_t kNextMidnight = 1783900800;
}

int main()
{
    setenv("TZ", "UTC0", 1);
    tzset();

    NetworkNtpScheduleState cold =
        initialize_network_ntp_schedule(false, kPlausibleNow);
    assert(cold.boot_due);
    assert(!cold.daily_pending);
    assert(cold.next_daily_at == 0);
    assert(cold.next_retry_at == 0);
    assert(cold.retry_failures == 0);

    NetworkNtpScheduleState synced =
        initialize_network_ntp_schedule(true, kPlausibleNow);
    assert(!synced.boot_due);
    assert(synced.next_daily_at == kNextMidnight);

    NetworkNtpRetryUpdate retry = finish_network_ntp_attempt(
        &cold, false, true, false, 100);
    assert(retry.scheduled);
    assert(retry.delay_seconds == 15);
    assert(cold.next_retry_at == 115);
    assert(cold.retry_failures == 1);
    retry = finish_network_ntp_attempt(&cold, false, true, false, 200);
    assert(retry.delay_seconds == 30);
    assert(cold.next_retry_at == 230);
    assert(cold.retry_failures == 2);

    NetworkNtpScheduleState valid_time_retry = {};
    retry = finish_network_ntp_attempt(
        &valid_time_retry, false, true, true, kPlausibleNow);
    assert(retry.scheduled);
    assert(retry.delay_seconds == 5 * 60);
    assert(valid_time_retry.next_retry_at == kPlausibleNow + 5 * 60);
    assert(valid_time_retry.retry_failures == 1);

    NetworkNtpScheduleState manual_only = {};
    manual_only.next_retry_at = 999;
    manual_only.retry_failures = 4;
    retry = finish_network_ntp_attempt(
        &manual_only, false, false, true, kPlausibleNow);
    assert(!retry.scheduled);
    assert(manual_only.next_retry_at == 0);
    assert(manual_only.retry_failures == 0);

    cold.daily_pending = true;
    retry = finish_network_ntp_attempt(
        &cold, true, true, true, kPlausibleNow);
    assert(!retry.scheduled);
    assert(!cold.boot_due);
    assert(!cold.daily_pending);
    assert(cold.next_retry_at == 0);
    assert(cold.retry_failures == 0);
    assert(cold.next_daily_at == kNextMidnight);

    synced.next_daily_at = kPlausibleNow;
    refresh_network_ntp_daily_due(&synced, kPlausibleNow, true);
    assert(synced.daily_pending);
    assert(synced.next_daily_at == 0);

    synced.daily_pending = false;
    synced.boot_due = false;
    refresh_network_ntp_daily_due(&synced, kPlausibleNow, true);
    assert(!synced.daily_pending);
    assert(synced.next_daily_at == kNextMidnight);

    setenv("TZ", "CST-8", 1);
    tzset();
    struct tm near_midnight = {};
    near_midnight.tm_year = 2026 - 1900;
    near_midnight.tm_mon = 8;
    near_midnight.tm_mday = 27;
    near_midnight.tm_hour = 23;
    near_midnight.tm_min = 59;
    near_midnight.tm_sec = 57;
    near_midnight.tm_isdst = -1;
    const time_t corrected_time = mktime(&near_midnight);
    assert(corrected_time != static_cast<time_t>(-1));
    const time_t midnight = next_local_midnight_time(corrected_time);
    const time_t following_midnight = next_local_midnight_time(midnight);

    struct NtpOrigin {
        bool daily_pending;
        bool boot_due;
        bool retry_required;
    };
    const NtpOrigin origins[] = {
        {true, false, true},
        {false, true, true},
        {false, false, false},
    };
    for (const NtpOrigin &origin : origins) {
        NetworkNtpScheduleState around_midnight = {};
        around_midnight.daily_pending = origin.daily_pending;
        around_midnight.boot_due = origin.boot_due;
        finish_network_ntp_attempt(&around_midnight,
                                   true,
                                   origin.retry_required,
                                   true,
                                   corrected_time);
        assert(around_midnight.next_daily_at == following_midnight);
        refresh_network_ntp_daily_due(&around_midnight,
                                      corrected_time + 4,
                                      true);
        assert(!around_midnight.daily_pending);

        NetworkSyncScheduleInput next_request = {};
        next_request.now = corrected_time + 4;
        next_request.next_ntp_retry_at = around_midnight.next_retry_at;
        next_request.daily_ntp_due = around_midnight.daily_pending;
        assert(!calculate_network_sync_schedule(next_request).ntp_due);
        next_request.manual_ntp_due = true;
        assert(calculate_network_sync_schedule(next_request).ntp_due);
    }

    NetworkNtpScheduleState cutoff = {};
    finish_network_ntp_attempt(&cutoff,
                               true,
                               false,
                               true,
                               midnight - 5 * 60 - 1);
    assert(cutoff.next_daily_at == midnight);
    finish_network_ntp_attempt(&cutoff,
                               true,
                               false,
                               true,
                               midnight - 5 * 60);
    assert(cutoff.next_daily_at == following_midnight);

    schedule_network_ntp_after_provisioning(&synced);
    assert(synced.boot_due);
    assert(!synced.daily_pending);
    assert(synced.next_daily_at == 0);
    assert(synced.next_retry_at == 0);
    assert(synced.retry_failures == 0);

    return 0;
}
