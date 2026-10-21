# 推送前敏感词检测

对齐国家互联网信息规定，避免违规内容经 PushPlus 发出。实现见 `sensitive.py`，与大盘新鲜度闸门同一套「零第三方依赖 + 环境变量」风格。

## 法规依据（公开条文，不编造名单）

- 《中华人民共和国网络安全法》
- 《互联网信息服务管理办法》
- 《网络信息内容生态治理规定》（国家互联网信息办公室令第 5 号）第六条、第七条

词库只覆盖法规列明的违法 / 不良信息类别：危害国家安全、分裂国家、恐怖主义与极端主义、民族仇恨、邪教、淫秽色情、赌博、毒品、违法枪爆、诈骗传销、暴力教唆、侵害未成年人。**不**维护政治人物名单，也**不**把正常财经 / 地缘 / 监管新闻当违规。

下列用语**不在词库里**，因此不会误拦演示数据与日常快讯：

战争、制裁、Iran、立案、骗局、Ponzi、Terrorism Risk Insurance、Drug Transit、博彩、六合彩、独立。

## 两层闸门

1. `filter_brief(brief)`：按条扫描快讯标题。违规条目剔除，合规语境（打击 / 查处 / 反对 / charges 等执法、驳斥口径）下的报道保留。源的 key 始终保留（可能变成空列表），以免打乱 `build_html` 的板块分组。
2. `check_push(title, content)`：再扫推送标题 + HTML 正文（先剥标签再匹配，`<span>` 切开的词也能命中）。残留命中则取消整次推送。

调用顺序（`push_brief.py` / `POST /api/run` 相同）：

```
collect_all()
  → 大盘新鲜度检查（不通过：退出码 3 / HTTP 409）
  → filter_brief()          # 按条剔除
  → build_html()
  → check_push(title, html) # 残留则取消
  → 不通过：退出码 4 / HTTP 422
  → 通过：POST PushPlus
```

## 匹配规则

| 规则 | 作用 |
| --- | --- |
| 全角 → 半角、ASCII 小写、去零宽字符 | `Ｐｏｒｎｈｕｂ`、`网\u200b络赌场` 仍命中 |
| 中文忽略夹杂空格 / 符号 | `网 络 赌 场`、`网*络*赌*场` 仍命中 |
| 英文按单词边界 | `crisis` 不会误伤 `isis` |
| 强合规语境 | 打击 / 查处 / 反对 / 谴责 / charges / crackdown … 高、中严重度都可放行 |
| 弱合规语境 | 风险 / 监管 / 警示 只放行中严重度（赌博、色情、诈骗报道）；台独、恐怖主义等仍拦截 |

高严重度类别：危害国家安全、分裂国家、恐怖主义、邪教、违法枪爆、侵害未成年人。

## 环境变量

| 变量 | 作用 |
| --- | --- |
| `SKIP_SENSITIVE_CHECK=1` | 跳过检测（测试 / 应急，不建议日常开启） |
| `SENSITIVE_FORCE=pass\|block` | 强制闸门结果（联调） |
| `SENSITIVE_LEXICON=/path/to.txt` | 外掛词库。一行一词；`block:词` 或 `national_security:词` 为高严重度；`#` 开头为注释 |

## 接口与退出码

| 入口 | 未通过时 |
| --- | --- |
| `push_brief.py` | 退出码 4，打印 `reason` 与命中列表 |
| `POST /api/run` | HTTP 422，JSON 含 `message` 与 `sensitive` |
| `GET /api/sensitive` | 只诊断、不修改；返回 `ok / dropped_count / dropped / categories / lexicon_size` |

页面手动推送按钮在 422 时把服务端 `message` 以 toast 展示（与 409 一样停留 6 秒）。

## 测试

```bash
python3 test_sensitive.py
python3 test_push_gate.py
python3 test_server.py
```

内置演示数据 `_DEMO`（含知乎「离谱的骗局」、SEC Ponzi Scheme、GovTrack Terrorism Risk Insurance、总统 Drug Transit 文件）必须全部放行，否则离线推送会被误拦。
