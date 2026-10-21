# 章鱼 AI 简报 → 400×300 设备图（多页轮播版 v1.8.0）

把 [k-macao/002](https://github.com/k-macao/002) 的「全景分析」渲染成设备直接可用的多页 1bpp 位图，每页 1 分钟自动轮播，完整展示全量内容。

![设备简报样张（示例数据）](sample.png)

## 字体与层级（v1.8.0 思源黑体）

- **统一思源黑体、常规字重**：标题、正文、标签、数字和符号全部使用 Source Han Sans / Noto Sans SC，不加粗、不描边、不叠影。渲染机优先读取 `device_brief/fonts/SourceHanSansSC-Regular.ttf`（17MB 变量字体，Google Fonts 同款思源黑体），并显式选择变量字体的 `Regular` 字重；该字体文件自身默认值是 `Thin`，若不切换字重，1bpp 二值化会让汉字笔画断裂。可通过 `SOURCE_HAN_SANS_FONT` 环境变量覆盖路径；若不存在则回退到 `MiSans-Medium.ttf`。排版测试会检查变量字体确实使用 `Regular` 实例。
- **字号层级**：标题 `18px` → 分栏 `14px` → 正文 `13px` → 次要 `12px` → 页眉 `11px` → 最小 `10px`，通过字号和留白建立层级，不使用粗体。
- 全部内容完整显示：旧版放不下删行，新版改为分页，单文件最多 30 页（450KB），设备每 60 秒自动切下一页。

排版按实物可读性定字号：4.2\" 面板 400×300（约 120dpi），正文 13px、次要信息 12px、全页最小 10px。字号下限由 `test_render_brief.py` 守住：

```bash
python3 device_brief/test_render_brief.py
```

## 用法

```bash
python3 -m pip install pillow
python3 device_brief/render_brief.py --out-dir aibrief --demo
```

输出：

- `aibrief/latest.bin`：设备负载（`AIB1` + 宽高 + N*15000 字节位图，N=页数，固件支持 1..30 页）。
- `aibrief/latest.png`：第一页 PNG 预览。
- `aibrief/page_*.png`：每页 PNG 预览。
- `aibrief/latest_combined.png`：垂直拼接总览图。
- `aibrief/meta.json`：生成时间、页数、字体、实时源数量、sha256。

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--demo` | 不联网，用内置样例数据渲染，用于排版调试 |
| `--require-live` | 实时源少于阈值时直接失败，供定时任务使用 |
| `--min-live-sources N` | 实时源阈值，默认 20 |
| `--limit N` | 限制每个来源抓取的条目数 |
| `--pages N` | 限制最大页数（0 不限制，固件上限 30） |

## 内容分页（002 全量）

1. **AI 每日总结**：市场情绪、多空博弈、板块方向、资金流向
2. **AI 看盘·A股**：三大指数、成交额、涨跌家数、领涨领跌、主力资金
3. **AI 看盘·港股**：恒生/科技/国企、成交额、南向资金
4. **AI 看盘·美股**：道指/标普/纳指、成交额、资金避险
5. **AI 情绪热力图**：Top 40 关键词热度、情绪、ETF 映射（分页）
6. **AI 政策分析**：8 大政策维度、鹰鸽取向、净多空
7. **AI 板块机会**：机会方向 Top4 + 承压方向 Top2，含证据与 ETF
8. **全网快讯**：6 大板块 69 源，每源 3 条，跨源去重，按板块分页完整显示

> 渲染机需要能访问 002 的 69 个数据源。GitHub Actions（`.github/workflows/aibrief.yml`）每天 07:10（北京时间）运行一次；本机运行后把 `aibrief/` 提交回仓库，设备会在 5 分钟内拿到新图（多页 450KB，30s 超时）。设备固件开机即联网拉取多页并常驻显示，每 60 秒自动翻页，之后每 5 分钟重新拉取一次。
