// 实现温湿历史页不依赖 LVGL 的数值范围、坐标映射和文本格式化。
#include "ui_history_format.h"

#include "ui_text_format.h"

namespace {
constexpr float kMinimumPlotRange = 0.01f;
constexpr float kFallbackPlotRange = 1.0f;
constexpr float kPlotRoundOffset = 0.5f;
constexpr float kTemperatureAxisPad = 0.6f;
constexpr float kHumidityAxisPad = 3.0f;
constexpr float kTemperatureFlatRangeThreshold = 1.0f;
constexpr float kHumidityFlatRangeThreshold = 5.0f;
constexpr float kTemperatureFlatRangeExtraPad = 0.5f;
constexpr float kHumidityFlatRangeExtraPad = 2.0f;
constexpr float kAxisMidpointRatio = 0.5f;
constexpr const char *kAxisHourFormat = "%02d:00";
constexpr const char *kTimePlaceholder = "--:--";
constexpr const char *kAxisPlaceholder = "--";
constexpr const char *kTemperatureAxisFormat = "%.0f℃";
constexpr const char *kHumidityAxisFormat = "%.0f%%";
constexpr const char *kTemperatureBadgeFormat = "%.1f";
constexpr const char *kHumidityBadgeFormat = "%.0f";

static_assert(kMinimumPlotRange > 0.0f, "history minimum plot range must be positive");
static_assert(kFallbackPlotRange >= kMinimumPlotRange,
              "history fallback range must cover the minimum range");
static_assert(kTemperatureAxisPad > 0.0f && kHumidityAxisPad > 0.0f,
              "history axis padding must be positive");
static_assert(kTemperatureFlatRangeThreshold > 0.0f &&
                  kHumidityFlatRangeThreshold > 0.0f,
              "history flat range thresholds must be positive");

float history_curve_tangent(const HistoryCurvePoint &left,
                            const HistoryCurvePoint &center,
                            const HistoryCurvePoint &right)
{
    const float left_span = static_cast<float>(center.x - left.x);
    const float right_span = static_cast<float>(right.x - center.x);
    const float left_slope = (center.value - left.value) / left_span;
    const float right_slope = (right.value - center.value) / right_span;
    if (left_slope == 0.0f || right_slope == 0.0f ||
        (left_slope < 0.0f) != (right_slope < 0.0f)) {
        return 0.0f;
    }
    // Weighted harmonic tangents round extrema without inventing new peaks.
    const float left_weight = 2.0f * right_span + left_span;
    const float right_weight = right_span + 2.0f * left_span;
    return (left_weight + right_weight) /
           (left_weight / left_slope + right_weight / right_slope);
}
} // namespace

HistoryAxisRange history_axis_range(bool temperature, float minimum, float maximum)
{
    float pad = temperature ? kTemperatureAxisPad : kHumidityAxisPad;
    float flat_threshold = temperature ? kTemperatureFlatRangeThreshold
                                       : kHumidityFlatRangeThreshold;
    if (maximum - minimum < flat_threshold) {
        pad += temperature ? kTemperatureFlatRangeExtraPad : kHumidityFlatRangeExtraPad;
    }
    HistoryAxisRange range = {};
    range.minimum = minimum - pad;
    range.maximum = maximum + pad;
    range.midpoint = (range.minimum + range.maximum) * kAxisMidpointRatio;
    return range;
}

int history_value_to_plot_y(float value, float minimum, float maximum, int y, int height)
{
    if (height <= 0) {
        return y;
    }
    float range = maximum - minimum;
    if (range < kMinimumPlotRange) {
        range = kFallbackPlotRange;
    }
    float normalized = (value - minimum) / range;
    int offset = static_cast<int>(normalized * (height - 1) + kPlotRoundOffset);
    return y + height - 1 - offset;
}

HistoryCurveSegment history_curve_segment(const HistoryCurvePoint &from,
                                          const HistoryCurvePoint &to,
                                          const HistoryCurvePoint *before,
                                          const HistoryCurvePoint *after)
{
    HistoryCurveSegment segment = {from.value, to.value, 0.0f, 0.0f};
    if (to.x <= from.x) {
        return segment;
    }
    const float span = static_cast<float>(to.x - from.x);
    const float slope = (to.value - from.value) / span;
    segment.from_tangent = span *
        (before && before->x < from.x
             ? history_curve_tangent(*before, from, to)
             : slope);
    segment.to_tangent = span *
        (after && after->x > to.x
             ? history_curve_tangent(from, to, *after)
             : slope);
    return segment;
}

float history_curve_value(const HistoryCurveSegment &segment, float position)
{
    if (position <= 0.0f) {
        return segment.from;
    }
    if (position >= 1.0f) {
        return segment.to;
    }
    const float squared = position * position;
    const float cubed = squared * position;
    const float value = (2.0f * cubed - 3.0f * squared + 1.0f) * segment.from +
                        (cubed - 2.0f * squared + position) * segment.from_tangent +
                        (-2.0f * cubed + 3.0f * squared) * segment.to +
                        (cubed - squared) * segment.to_tangent;
    const float minimum = segment.from < segment.to ? segment.from : segment.to;
    const float maximum = segment.from > segment.to ? segment.from : segment.to;
    return value < minimum ? minimum : (value > maximum ? maximum : value);
}

void format_history_axis_hour(time_t value, char *out, size_t out_len)
{
    if (!ui_text::output_buffer_available(out, out_len)) {
        return;
    }
    struct tm local = {};
    localtime_r(&value, &local);
    ui_text::format_or_fallback(out,
                                out_len,
                                kTimePlaceholder,
                                kAxisHourFormat,
                                local.tm_hour);
}

void format_history_axis_value(bool temperature, float value, char *out, size_t out_len)
{
    ui_text::format_or_fallback(out,
                                out_len,
                                kAxisPlaceholder,
                                temperature ? kTemperatureAxisFormat : kHumidityAxisFormat,
                                value);
}

void format_history_badge_value(bool temperature, float value, char *out, size_t out_len)
{
    ui_text::format_or_fallback(out,
                                out_len,
                                kAxisPlaceholder,
                                temperature ? kTemperatureBadgeFormat : kHumidityBadgeFormat,
                                value);
}
