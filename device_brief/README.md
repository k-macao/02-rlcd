# 章鱼 AI 简报 → 400×300 设备图

把 [k-macao/05](https://github.com/k-macao/05) 的「全景分析」渲染成设备直接可用的 1bpp 位图。

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
