// 验证温湿历史页坐标范围、像素映射和显示文本保持稳定。
#include "ui_history_format.h"

#include <assert.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

namespace {
constexpr float kFloatTolerance = 0.001f;

bool near(float actual, float expected)
{
    return fabsf(actual - expected) < kFloatTolerance;
}

void check_curve(const HistoryCurvePoint &from,
                 const HistoryCurvePoint &to,
                 const HistoryCurvePoint *before = nullptr,
                 const HistoryCurvePoint *after = nullptr)
{
    const HistoryCurveSegment segment = history_curve_segment(from, to, before, after);
    assert(history_curve_value(segment, -1.0f) == from.value);
    assert(history_curve_value(segment, 0.0f) == from.value);
    assert(history_curve_value(segment, 1.0f) == to.value);
    assert(history_curve_value(segment, 2.0f) == to.value);
    const float minimum = fminf(from.value, to.value);
    const float maximum = fmaxf(from.value, to.value);
    float previous = from.value;
    for (int i = 1; i <= 100; ++i) {
        const float value = history_curve_value(segment, i / 100.0f);
        assert(isfinite(value));
        assert(value >= minimum && value <= maximum);
        if (to.value >= from.value) {
            assert(value + kFloatTolerance >= previous);
        } else {
            assert(value - kFloatTolerance <= previous);
        }
        previous = value;
    }
}

void test_history_curves()
{
    const HistoryCurvePoint flat[] = {{0, 22.0f}, {11, 22.0f}, {23, 22.0f}, {34, 22.0f}};
    check_curve(flat[1], flat[2], &flat[0], &flat[3]);
    const HistoryCurvePoint ramp[] = {{0, -10.0f}, {10, 0.0f}, {20, 10.0f}, {30, 20.0f}};
    const auto linear = history_curve_segment(ramp[1], ramp[2], &ramp[0], &ramp[3]);
    assert(near(history_curve_value(linear, 0.25f), 2.5f));
    assert(near(history_curve_value(linear, 0.75f), 7.5f));
    check_curve(ramp[1], ramp[2], &ramp[0], &ramp[3]);
    check_curve(ramp[1], ramp[2]);

    const HistoryCurvePoint peak[] = {{0, 20.0f}, {11, 25.0f}, {23, 20.0f}};
    const auto rising = history_curve_segment(peak[0], peak[1], nullptr, &peak[2]);
    const auto falling = history_curve_segment(peak[1], peak[2], &peak[0], nullptr);
    assert(rising.to_tangent == 0.0f && falling.from_tangent == 0.0f);
    assert(history_curve_value(rising, 0.9f) > 24.5f);
    assert(history_curve_value(falling, 0.1f) > 24.5f);
    check_curve(peak[0], peak[1], nullptr, &peak[2]);
    check_curve(peak[1], peak[2], &peak[0], nullptr);

    const HistoryCurvePoint invalid_before{11, 99.0f};
    const HistoryCurvePoint invalid_after{22, -99.0f};
    check_curve(flat[1], flat[2], &invalid_before, &invalid_after);
    const auto zero_width = history_curve_segment(flat[1], flat[1]);
    assert(history_curve_value(zero_width, 0.5f) == 22.0f);

    // Cover alternating extrema, plateaus and very unequal neighbouring slopes.
    constexpr float values[] = {-40.0f, 0.0f, 0.01f, 25.0f, 99.9f, 100.0f};
    for (float a : values) {
        for (float b : values) {
            for (float c : values) {
                for (float d : values) {
                    const HistoryCurvePoint points[] = {{0, a}, {11, b}, {23, c}, {34, d}};
                    check_curve(points[1], points[2], &points[0], &points[3]);
                }
            }
        }
    }
}
} // namespace

int main()
{
    setenv("TZ", "Asia/Shanghai", 1);
    tzset();

    test_history_curves();

    HistoryAxisRange temperature = history_axis_range(true, 20.0f, 20.5f);
    assert(near(temperature.minimum, 18.9f));
    assert(near(temperature.midpoint, 20.25f));
    assert(near(temperature.maximum, 21.6f));

    HistoryAxisRange humidity = history_axis_range(false, 50.0f, 52.0f);
    assert(near(humidity.minimum, 45.0f));
    assert(near(humidity.midpoint, 51.0f));
    assert(near(humidity.maximum, 57.0f));

    assert(history_value_to_plot_y(0.0f, 0.0f, 10.0f, 10, 62) == 71);
    assert(history_value_to_plot_y(5.0f, 0.0f, 10.0f, 10, 62) == 40);
    assert(history_value_to_plot_y(10.0f, 0.0f, 10.0f, 10, 62) == 10);
    assert(history_value_to_plot_y(8.0f, 8.0f, 8.0f, 12, 20) == 31);
    assert(history_value_to_plot_y(5.0f, 0.0f, 10.0f, 14, 0) == 14);

    struct tm local = {};
    local.tm_year = 126;
    local.tm_mon = 6;
    local.tm_mday = 12;
    local.tm_hour = 7;
    time_t value = mktime(&local);
    char text[16] = {};
    format_history_axis_hour(value, text, sizeof(text));
    assert(strcmp(text, "07:00") == 0);

    format_history_axis_value(true, -3.6f, text, sizeof(text));
    assert(strcmp(text, "-4℃") == 0);
    format_history_axis_value(false, 58.4f, text, sizeof(text));
    assert(strcmp(text, "58%") == 0);
    format_history_badge_value(true, 25.64f, text, sizeof(text));
    assert(strcmp(text, "25.6") == 0);
    format_history_badge_value(false, 58.4f, text, sizeof(text));
    assert(strcmp(text, "58") == 0);

    char short_text[3] = {};
    format_history_axis_value(true, 25.0f, short_text, sizeof(short_text));
    assert(strcmp(short_text, "--") == 0);
    format_history_badge_value(false, 50.0f, nullptr, 0);
    return 0;
}
