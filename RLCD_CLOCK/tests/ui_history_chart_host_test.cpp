// 验证生产温湿曲线经过原始采样点，缺测断线且不跨时间缺口取平滑切线。
#include "ui_history_chart.h"
#include "ui_history_format.h"
#include "app_metadata.h"

#include <algorithm>
#include <cassert>
#include <cstdio>
#include <vector>

const char *const TAG = "history-test";

namespace {
struct Line { int x0; int y0; int x1; int y1; };
struct Marker { int x; int y; };
std::vector<Line> lines;
std::vector<Marker> markers;
constexpr int kPlotX = 34;
constexpr int kPlotY = 10;
constexpr int kPlotWidth = 276;
constexpr int kPlotHeight = 62;

HourlySensorSample sample(int hour, float value, bool valid = true)
{
    HourlySensorSample result = {};
    result.timestamp = hour * 3600;
    result.temperature = value;
    result.humidity = value;
    result.valid = valid;
    return result;
}

template <size_t Count>
void draw(const HourlySensorSample (&samples)[Count], bool temperature = true)
{
    lines.clear();
    markers.clear();
    lv_obj_t canvas = {};
    lv_obj_t *axis[3] = {};
    draw_history_chart_panel(&canvas, 364, 190, samples, Count, temperature,
                             kPlotX, kPlotY, kPlotWidth, kPlotHeight,
                             nullptr, nullptr, axis);
    assert(!lines.empty());
    assert(lines.front().y0 == kPlotY + kPlotHeight);
    lines.erase(lines.begin()); // Exclude the unchanged axis baseline.
    for (const auto &line : lines) {
        assert(line.x1 == line.x0 + 1);
        assert(line.x0 >= kPlotX && line.x1 <= kPlotX + kPlotWidth);
        assert(line.y0 >= kPlotY && line.y0 < kPlotY + kPlotHeight);
        assert(line.y1 >= kPlotY && line.y1 < kPlotY + kPlotHeight);
    }
}
} // namespace

int clamp_int(int value, int minimum, int maximum)
{
    return std::max(minimum, std::min(maximum, value));
}
void canvas_draw_dashed_hline(lv_obj_t *, int, int, int, int, int, lv_color_t) {}
void canvas_draw_line(lv_obj_t *, int, int, int x0, int y0, int x1, int y1, lv_color_t)
{
    lines.push_back({x0, y0, x1, y1});
}
void canvas_draw_filled_circle(lv_obj_t *, int, int, int x, int y, int, lv_color_t)
{
    markers.push_back({x, y});
}
bool set_obj_visible(lv_obj_t *, bool) { return false; }
bool set_label_text_if_changed(lv_obj_t *, const char *) { return false; }

int main()
{
    const HourlySensorSample peak[] = {sample(1, 20), sample(2, 25), sample(3, 20)};
    draw(peak);
    const auto axis = history_axis_range(true, 20, 25);
    const int peak_y = history_value_to_plot_y(25, axis.minimum, axis.maximum,
                                               kPlotY, kPlotHeight);
    const int peak_x = kPlotX + 2 * kPlotWidth / 24;
    assert(lines.size() == 23);
    assert(markers.size() == 2);
    assert(markers[0].x == peak_x && markers[0].y == peak_y);
    assert(lines.front().y0 == history_value_to_plot_y(20, axis.minimum, axis.maximum,
                                                      kPlotY, kPlotHeight));
    assert(lines.back().y1 == lines.front().y0);
    const int rising_pixels = peak_x - lines.front().x0;
    assert(lines[rising_pixels - 1].x1 == peak_x &&
           lines[rising_pixels - 1].y1 == peak_y);
    assert(lines[rising_pixels].x0 == peak_x && lines[rising_pixels].y0 == peak_y);

    const HourlySensorSample missing[] = {
        sample(1, 20), sample(2, 25), sample(3, 99, false),
        sample(4, 25), sample(5, 20)};
    draw(missing);
    assert(lines.size() == 23);
    for (const auto &line : lines) {
        assert(line.x1 <= peak_x || line.x0 >= kPlotX + 4 * kPlotWidth / 24);
    }
    const auto separate_lines = lines;
    const HourlySensorSample other_side[] = {
        sample(1, 20), sample(2, 25), sample(3, -99, false),
        sample(4, 21), sample(5, 20)};
    // Same extrema keep the axis fixed; the gap must isolate the left tangent.
    draw(other_side);
    for (int i = 0; i < rising_pixels; ++i) {
        assert(lines[i].x0 == separate_lines[i].x0);
        assert(lines[i].y0 == separate_lines[i].y0);
        assert(lines[i].x1 == separate_lines[i].x1);
        assert(lines[i].y1 == separate_lines[i].y1);
    }

    const HourlySensorSample long_gap[] = {sample(1, 20), sample(4, 25)};
    draw(long_gap);
    assert(lines.empty());
    const HourlySensorSample short_gap[] = {sample(1, 20), sample(3, 25)};
    draw(short_gap);
    assert(lines.size() == 23);
    const HourlySensorSample reversed[] = {sample(2, 20), sample(1, 25)};
    draw(reversed);
    assert(lines.empty());
    const HourlySensorSample duplicate[] = {sample(1, 20), sample(1, 25)};
    draw(duplicate);
    assert(lines.empty());
    const HourlySensorSample empty[] = {sample(1, 20, false), sample(2, 25, false)};
    draw(empty);
    assert(lines.empty() && markers.empty());
    const HourlySensorSample single[] = {sample(1, 20)};
    draw(single);
    assert(lines.empty() && markers.empty());
    draw(peak, false);
    assert(lines.size() == 23 && markers.size() == 2);
    std::puts("History chart production rendering tests passed");
}
