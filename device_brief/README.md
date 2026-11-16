# 全球行情数据面板 → 400×300 设备图

把 A、港、美、韩、日 12 个指数的最新点位、涨跌幅、成交量和成交额渲染成 RLCD 4.2 英寸设备可直接显示的一页 1bpp 位图。

![面板样张（2026-11-13 收盘数据）](sample.png)

新固件（v1.8.7 起）每分钟直连行情接口，在设备上自己画这张面板；这里的渲染器由 GitHub Actions 里的常驻循环每分钟运行一次，输出同款位图到仓库根目录的 `aibrief/`，供旧固件显示，并在设备直连失败时兜底。面板内容、页脚状态和两条更新链路的说明见 [`docs/Aibrief_zh.md`](../docs/Aibrief_zh.md)。

## 文件

| 文件 | 作用 |
| --- | --- |
| `market_quotes.py` | 数据层（只用标准库）：四个行情接口的地址、解析、单位换算、多源合并和数字格式 |
| `render_brief.py` | 排版与输出：画面板、排版预检、写设备负载和预览 |
| `mono_text.py` | FreeType 单色（MONO + 自动 hinting）文字渲染，字宽与固件的 LVGL 字库一致 |
| `fixtures_tool.py` | 生成 / 校验 `fixtures/` 里服务器与固件共用的期望值和基准图 |
| `fixtures/` | 留档的接口实测响应（2026-11-13 收盘；腾讯响应里港股两行的变量名已按实时代码改成 `v_r_hk…`，行内容未变），以及由它们生成的期望值和三张基准图 |
| `test_render_brief.py` | 渲染器回归测试，不联网 |
| `publish_loop.py` | 常驻发布循环（在 GitHub Actions 里运行）：每分钟渲染一次，有变化就提交推送；自己接力、给新代码让位 |
| `test_publish_loop.py` | 发布循环和两个工作流的回归测试，不联网（用临时 git 仓库和假的 `gh`） |
| `fonts/` | 阿里巴巴普惠体 3.0 Regular / Bold |

## 用法

```bash
python3 -m pip install pillow==13.0.0 freetype-py==2.5.1

# 回归测试（不联网）
python3 device_brief/test_render_brief.py
python3 device_brief/test_publish_loop.py

# 离线演示：用 fixtures/ 里留档的数据出图，顶栏会标「离线演示」
python3 device_brief/render_brief.py --demo --out-dir /tmp/market-panel

# 实抓行情并更新正式目录；有效行情不足时保留上一版
python3 device_brief/render_brief.py --out-dir aibrief --require-live
```

| 参数 | 说明 |
| --- | --- |
| `--out-dir DIR` | 输出目录，默认 `aibrief` |
| `--demo` | 不联网，使用 `fixtures/` 里的数据 |
| `--require-live` | 有效行情少于 `--min-rows` 行时退出 3，不写任何文件；演示数据不算有效行情 |
| `--min-rows N` | 12 行里至少多少行取到最新点位才更新，默认 9（与固件的 `kMinLiveRows` 相同） |
| `--no-preview` | 不生成 `latest.png`，并删掉已有的那张；每分钟发布时使用 |

退出码：`0` 成功（包括行情没变、无需更新），`3` 有效行情不足，`5` 排版预检未通过。后两种情况都保留上一版文件。

输出：

- `latest.bin`：设备负载，`AIB1` + 宽高 + 15000 字节位图，共 15008 字节。
- `version.txt`：设备比对用的小清单（`sha256` / `pages` / `bytes` / `generated_at`）。
- `meta.json`：每行的原始数值、单元格文字、数据来源和行情时间。
- `latest.png`：同一张图的 PNG 预览。带 `--no-preview` 时不生成：`main` 上每分钟发布一次，PNG 无法增量压缩，占每次提交体积的一半以上，而设备不读它。

行情与上一版逐位相同时，这些文件都不重写（`meta.json` 里的生成时间也不更新），工作流因此不会产生空提交。旧版多页简报留下的 `page_*.png`、`latest_combined.png`、`latest_contact_sheet.png` 会在下一次运行时被清掉。

## 数据源与合并规则

| 数据源 | 用途 |
| --- | --- |
| 腾讯证券 `qt.gtimg.cn` | A、美指数整行；港股指数的点位、涨跌幅、成交额（实时代码 `r_hkHSI` / `r_hkHSTECH`，不带 `r_` 的延迟 15 分钟）；日韩指数的点位（备用） |
| 新浪财经 `hq.sinajs.cn`（需要 `Referer`） | 港股指数的成交股数；腾讯缺行时提供整行 |
| Naver 金融 `polling.finance.naver.com` | KOSPI / KOSDAQ 整行；日经 225 成交量；东证指数整行 |

- 日经 225 盘中用腾讯的实时点位加 Naver 的成交量；Naver 标记收盘、腾讯缺行或两者点位相差超过 3% 时整行用 Naver。
- 东证指数只用 Naver（腾讯和新浪的 TOPIX 数据不可用）。
- 美股、日本指数没有可靠的成交额，固定显示 `--`。
- 点位不大于 0 或涨跌幅绝对值超过 50% 的行按脏数据丢弃。
- 单个数据源失败只影响它负责的行或列，不影响其余数据。

## 排版

- 画布 400×300，只有纯黑和纯白；全页阿里巴巴普惠体 3.0、16px，数字串、顶栏标题和市场色块用 Bold，其余用 Regular。
- 文字用 FreeType `FT_LOAD_TARGET_MONO + FT_LOAD_FORCE_AUTOHINT` 直接生成 1bpp 字形，落在整数像素上；字宽取 LVGL 字库的 `adv_w`（四舍五入到 1/16 像素），不做字距调整，这样服务器位图和固件自绘逐像素相同。
- 保存前预检：尺寸、字号、文字越界、文字互相重叠、灰阶像素，任何一项不通过都不写文件。
- 页脚右侧 `x=194..388`、`y=279..300` 留白，设备在这里写「每分钟更新」或「读取 HH:MM」。

## 改动版面、格式或数据源时

服务器和固件各有一份实现，必须同步改：

1. 改 `render_brief.py` / `market_quotes.py`，同时改固件的 `RLCD_CLOCK/main/ui/ui_market_panel_layout.h`、`ui_market_panel_render.cpp`、`RLCD_CLOCK/main/network/market_panel_contract.h`（接口地址在 `main/core/app_network_config.h`）。
2. 面板新增了文字：把字补进 `RLCD_CLOCK/tools/gen_market_panel_fonts.py` 的清单并重新生成字库。
3. `python3 device_brief/fixtures_tool.py --write` 重新生成期望值和基准图。
4. 运行 `test_render_brief.py` 和固件主机测试 `market_panel_contract_test`、`market_panel_view_test`（命令见 [`docs/Aibrief_zh.md`](../docs/Aibrief_zh.md)）。

## 每分钟发布（GitHub Actions）

GitHub 的定时任务做不到每分钟（本仓库 `*/5` 的定时器在 11 小时里只触发了两次），所以由 `publish_loop.py` 在 `.github/workflows/aibrief.yml` 里常驻运行：

```bash
# 默认分支：每 60 秒渲染一次，有变化就提交推送；一轮 5.5 小时，自己接力
python3 device_brief/publish_loop.py --out-dir aibrief --publish --no-preview

# 其他分支 / 本地：只渲染一次到指定目录，不提交
python3 device_brief/publish_loop.py --out-dir /tmp/market-panel --loop-seconds 0
```

`--publish` 只能在 GitHub Actions 里用（它会把工作区重置到远端最新提交）。

- **每个周期**：运行渲染器 → `aibrief/` 有变化就提交 `Update market panel device image [skip ci]` 并推送 → 查看有没有新的运行在排队 → 睡到下一个整周期。一次渲染拖过了周期时直接对齐到下一个周期，不补跑。
- **推送被拒绝**（`main` 上刚好多了别的提交）：把这次的画面叠到远端最新提交上重试，最多 3 次；仍失败就回到远端状态，下一个周期重新来过，不会留下只存在本地的提交。
- **行情不足**（渲染器退出码 3）或渲染超时：跳过这个周期，保留上一版。
- **接力**：单个任务最长 6 小时，一轮只跑 `LOOP_MINUTES`（330 分钟）。每一轮正常运行 5 分钟后就用 `workflow_dispatch` 预约下一轮；工作流里同一分支只允许一个循环在跑，预约的运行一直排队，等当前这轮结束（跑满、出错、被取消或执行机故障）才自动开始。收尾时发现没有后继在排队会再补触发一次。
- **让位**：比自己新、还没结束、而且不是上面那种预约接力的运行（代码推送、有人手动 Run workflow）出现时，这一轮立刻收尾，新代码最多等一个周期就接管。
- **看门狗**：`.github/workflows/aibrief-watchdog.yml` 定时检查，没有任何运行中或排队中的循环时把它重新拉起。
- **回归测试**：代码推送和手动触发时跑 `test_render_brief.py` 与 `test_publish_loop.py`；自动接力不重复跑。测试不拦发布，避免固件与渲染器的常量一时对不上就让设备图停更。
- **只有默认分支发布**：其他分支只渲染一次到临时目录，并上传为工作流产物 `market-panel-device-image`（含 PNG 预览）。

更新间隔在 `aibrief.yml` 的 `INTERVAL_SECONDS`。每分钟一次时，交易时段 `main` 上每个交易日约多 870 个提交；提交量、仓库体积和停止方法见 [`docs/Aibrief_zh.md`](../docs/Aibrief_zh.md)。
