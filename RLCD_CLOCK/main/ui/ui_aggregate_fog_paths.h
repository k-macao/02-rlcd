// 手工描绘浅雾带轮廓，在编译期复用贝塞尔采样生成固定逐列边界。
#pragma once
#include "ui_aggregate_cloud_paths.h"

namespace aggregate_fog_paths {
using Curve=aggregate_cloud_paths::Curve;
struct Band {
    aggregate_cloud_paths::Shape edge;
    uint8_t density;
};
inline constexpr int icon_bars[][3]={{2,22,10},{2,37,17},{0,33,24},{11,24,31}};

inline constexpr Curve top_left[]={
    {2,17,32,30,32,33},{17,40,33,33,35,37},
    {40,64,37,39,40,41},{64,88,41,44,44,45}};
inline constexpr Curve bottom_left[]={
    {2,17,45,47,48,47},{17,40,47,48,48,49},
    {40,64,49,49,49,48},{64,88,48,49,48,45}};
inline constexpr Curve top_middle[]={
    {86,104,35,34,31,32},{104,126,32,30,32,34},
    {126,146,34,34,35,36},{146,167,36,36,37,38}};
inline constexpr Curve bottom_middle[]={
    {86,104,35,40,41,41},{104,126,41,41,40,40},
    {126,146,40,40,40,39},{146,167,39,40,40,38}};
inline constexpr Curve top_right[]={
    {179,195,39,37,34,35},{195,219,35,32,34,37}};
inline constexpr Curve bottom_right[]={
    {179,195,39,44,48,47},{195,219,47,47,49,50}};
inline constexpr Curve top_mid_left[]={
    {38,62,75,74,70,70},{62,83,70,70,72,73}};
inline constexpr Curve bottom_mid_left[]={
    {38,62,75,80,81,79},{62,83,79,79,75,73}};
inline constexpr Curve top_mid_right[]={
    {201,210,62,59,59,60},{210,219,60,60,61,61}};
inline constexpr Curve bottom_mid_right[]={
    {201,210,62,68,70,69},{210,219,69,68,69,68}};
inline constexpr Curve top_low_left[]={
    {35,56,94,91,88,86},{56,79,86,84,84,86},
    {79,105,86,88,91,93},{105,137,93,94,96,96}};
inline constexpr Curve bottom_low_left[]={
    {35,56,94,97,99,99},{56,79,99,99,99,99},
    {79,105,99,99,99,99},{105,137,99,99,98,96}};
inline constexpr Curve top_low_middle[]={
    {135,155,95,91,91,92},{155,184,92,94,95,96}};
inline constexpr Curve bottom_low_middle[]={
    {135,155,95,98,97,96},{155,184,96,99,98,96}};
inline constexpr Curve top_low_right[]={
    {170,190,95,90,88,86},{190,207,86,85,82,84},
    {207,219,84,85,83,84}};
inline constexpr Curve bottom_low_right[]={
    {170,190,95,98,99,99},{190,207,99,99,98,98},
    {207,219,98,99,98,98}};

inline constexpr Band bands[]={
    {aggregate_cloud_paths::make_shape(top_left,bottom_left),44},
    {aggregate_cloud_paths::make_shape(top_middle,bottom_middle),34},
    {aggregate_cloud_paths::make_shape(top_right,bottom_right),46},
    {aggregate_cloud_paths::make_shape(top_mid_left,bottom_mid_left),40},
    {aggregate_cloud_paths::make_shape(top_mid_right,bottom_mid_right),36},
    {aggregate_cloud_paths::make_shape(top_low_left,bottom_low_left),30},
    {aggregate_cloud_paths::make_shape(top_low_middle,bottom_low_middle),6},
    {aggregate_cloud_paths::make_shape(top_low_right,bottom_low_right),32}};

inline int coverage(int x,int y) {
    if(x<2 || x>219 || y<28 || y>98)return 0;
    int result=0;
    for(const auto &band:bands) {
        const int top=band.edge.top[x],bottom=band.edge.bottom[x];
        if(top==0 || bottom==120 || y<=top || y>=bottom)continue;
        const int height=bottom-top;
        const int ink=4*band.density*(y-top)*(bottom-y)/(height*height);
        if(ink>result)result=ink;
    }
    return result;
}
static_assert(sizeof(bands)<=4096,"fog contours must stay small read-only data");
} // namespace aggregate_fog_paths
