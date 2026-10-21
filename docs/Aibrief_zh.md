# 章鱼 AI 简报页（多页轮播 v1.8.0）

设备常驻的辅助页面，用多张 400×300 的单色图，把 [k-macao/002](https://github.com/k-macao/002)「章鱼 AI 全景分析」全量内容直接显示在 RLCD 上，每页 1 分钟自动轮播。

## 打开与退出

固件 v1.7.1 起为常驻模式，v1.8.0 起为多页轮播常驻：

1. 开机即自动联网拉取多页简报并全屏显示，不再进入时钟工作页。
2. 每 60 秒自动翻到下一页，完整展示全部内容（最多 30 页）。
3. **长按 KEY** 进入「设置」，设置关闭后回到简报页。

## 页面内容（002 全量，思源黑体）

简报图由服务器端排版，每页固定 400×300、1 位单色，字体统一思源黑体（Source Han Sans / Noto Sans SC）：

- **AI 每日总结**：多空比例与信号数、总结句、板块方向（利好 / 承压，带净信号与源数）、资金流向
- **AI 看盘·A股**：上证 / 深证 / 创业板 / 科创 50 涨跌、成交额、涨停/跌停、领涨领跌
- **AI 看盘·港股**：恒生 / 恒生科技 / 国企指数、成交额、南向资金
- **AI 看盘·美股**：道指 / 标普 / 纳指、成交额、资金避险
- **AI 情绪热力图**：Top 40 关键词热度、情绪、ETF 映射（分页展示）
- **AI 政策分析**：8 大政策维度、鹰鸽取向、净多空、支撑新闻
- **AI 板块机会**：机会方向 Top4 + 承压方向 Top2，含证据与港股 ETF 映射
- **全网快讯**：6 大板块 69 源，每源 3 条，跨源去重，按板块分页完整显示

设备图正文 13px（4.2" 面板约 120dpi，汉字约 2.7mm 高），思源黑体常规字重，全部内容分页完整显示，不再删行。

## 刷新与轮播策略

- 开机立即请求一次联网刷新，之后每 **5 分钟** 检查是否需要刷新（`kMaxRefreshAgeSeconds=300`）。
- 分页轮播：每 **60 秒** 自动切下一页（`kPageRotateSeconds=60`），`page_count` 最多 30 页，循环轮播。
- 每次刷新最多尝试 **2 次**：主源为 `raw.githubusercontent.com`，失败后回退到 jsDelivr 镜像；超时放宽到 30 秒以支持 450KB 多页文件。
- 下载与解析失败时保留上一版图像，只在串口日志中记录原因，不影响其它页面。
- **离线模式**与**低电量模式**下不会主动发起刷新请求，但仍保留轮播（低电量轮询间隔 60 秒）。
- 缓存保存在 PSRAM（最多 30 页，450KB），重启后保留；首次开机、尚未获取时显示「章鱼AI 简报尚未获取 / 长按 KEY 返回 · 自动每分钟翻页」。

## 数据与图像来源

仓库根目录的 `aibrief/latest.bin` 即设备拉取的负载（多页）：

```
偏移  长度        含义
0     4           魔数 "AIB1"
4     2           宽度（小端，固定 400）
6     2           高度（小端，固定 300）
8     N*15000     N 张 1bpp 位图，逐行 MSB 优先，bit=1 表示黑，N=页数（1..30）
```

- 单页兼容：旧版 15008 字节（N=1）仍可解析，新版解析器自动识别页数 = (文件长度-8)/15000
- `aibrief/meta.json` 记录生成时间、页数、字体、实时源数量、头条与校验值
- `aibrief/page_*.png` 每页预览，`latest_combined.png` 垂直拼接总览
- 图像由 `device_brief/render_brief.py` 生成（002 数据源 + 思源黑体），用法见 `device_brief/README.md`

## 固件接口

| 文件 | 作用 |
| --- | --- |
| `main/network/aibrief_contract.h` | 负载尺寸、魔数、多页解析（1..30）、刷新与轮播间隔 |
| `main/network/aibrief_state.*` | 多页位图缓存（30×15000）、版本号、当前页、页面请求状态、自动翻页 |
| `main/network/aibrief_service.*` | HTTPS 二进制下载（450KB 缓冲）、多页校验、发布 |
| `main/ui/ui_aibrief_page.*` | 多页 canvas 逐像素绘制、页码指示器、60秒自动翻页 |
| `main/ui/ui_views.cpp` | 页面轮询与返回逻辑（常驻简报页） |

同步使用独立事件位 `kAibriefSyncBit`，与天气、说说的窗口互不阻塞；请求在离线、低电量、上下文变化时会立即取消。

## 版本号自动递增

- `RLCD_CLOCK/CMakeLists.txt` 的 `PROJECT_VER` 为当前版本
- `.github/scripts/bump_version.py` 自动递增 PATCH（v1.8.0 -> v1.8.1）
- `build-release.yml` 在 tag 推送构建成功后，自动 bump 版本并推送到 main（`[skip ci]`）
- 手动指定版本：`python3 .github/scripts/bump_version.py --set v1.9.0`

## 首次把设备升级到本仓库固件

1. 打开[在线上位机](https://wickenzh.github.io/ESP32-S3-RLCD-4.2/)，用 Web Serial 连接设备。
2. 在「自定义资源」中新增 `type=4` 文本，内容填 `https://raw.githubusercontent.com/k-macao/04-rlcd/main`（会自动规范化为 `/firmware/latest.json`）。
3. 写入资源后重启设备。
4. 设备进入「设置 → 系统 → 检查更新」，选择 v1.8.0 安装；安装完成会自动重启。

备用清单为 `https://cdn.jsdelivr.net/gh/k-macao/04-rlcd@main/firmware/latest.json`，主源不可达时可改填该地址。
