# 章鱼 AI 简报 → 400×300 设备图

把 [k-macao/05](https://github.com/k-macao/05) 的「全景分析」渲染成设备直接可用的 1bpp 位图。

![设备简报样张（示例数据）](sample.png)

排版按实物可读性定字号：4.2" 面板 400×300（约 120dpi），正文 13px、次要信息 12px、全页最小 10px，不再用更小的字（旧版正文 9~10px、页脚 7px，实物上看不清）。放不下的内容删行不缩字：设备图不显示与标题区重复的「多空博弈」行和与板块行重复的「资金流向」行，原「AI 板块机会」卡片的净信号/源数并进「板块方向」行；网页版与推送不受影响，仍是全文。字号下限与不溢出由 `test_render_brief.py` 守住，改排版先跑它：

```bash
python3 device_brief/test_render_brief.py
```

## 用法

```bash
python3 -m pip install pillow
python3 device_brief/render_brief.py --out-dir aibrief
```

输出：

- `aibrief/latest.bin`：设备负载（`AIB1` + 宽高 + 15000 字节位图）。
- `aibrief/latest.png`：同图的 PNG 预览。
- `aibrief/meta.json`：生成时间、多空比例、实时源数量、头条与 sha256。

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--demo` | 不联网，用内置样例数据渲染，用于排版调试 |
| `--require-live` | 实时源少于阈值时直接失败，供定时任务使用 |
| `--min-live-sources N` | 实时源阈值，默认 20 |
| `--limit N` | 限制每个来源抓取的条目数 |

> 渲染机需要能访问 05 的 69 个数据源。GitHub Actions（`.github/workflows/aibrief.yml`）每天 07:10（北京时间）运行一次；本机运行后把 `aibrief/` 提交回仓库，设备即可在 6 小时内拿到新图。
