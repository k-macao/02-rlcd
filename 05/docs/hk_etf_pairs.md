# AI 内容的港股 ETF 双标的口径

> 更新时间：**2026-10-03**  
> 实现：`sources._HK_ETF_CATALOG`、`sources.hk_etf_pair()`、`analyze_keyword_heatmap()`、`analyze_brief()`

## 输出规则

1. 每个 AI 情绪热力图关键词与每个 AI 板块机会方向，固定返回 **2 个港交所现有 ETF 的 HKD 柜台**。
2. 每只 ETF 同时返回 `code`、`name`、`benchmark`（对应指数 / 参考标的）和 `mapping`（本次主题映射口径）。
3. 不再输出 A 股个股二选一组合。
4. 港股没有纯主题 ETF 时必须在 `mapping` 中写明“代理”，不能把相关行业 ETF 伪称为直接跟踪产品。
5. ETF 组合仅用于主题观察；不代表两只产品应等权配置，也不构成投资建议。

接口字段示例：

```json
{
  "keyword": "数据中心",
  "concept": "港股 ETF 双标的：02826.HK × 03140.HK",
  "etf_pair": [
    {
      "code": "02826.HK",
      "name": "Global X 中国云计算 ETF",
      "benchmark": "Solactive China Cloud Computing Index NTR",
      "mapping": "中国云计算 + 港美 AI"
    },
    {
      "code": "03140.HK",
      "name": "华夏港美人工智能 ETF",
      "benchmark": "Solactive G2 AI 50 Select Index NTR",
      "mapping": "中国云计算 + 港美 AI"
    }
  ]
}
```

## 主题默认组合

| AI 主题 | ETF 1 | ETF 2 | 映射口径 |
| --- | --- | --- | --- |
| AI 算力 | 03140.HK 华夏港美人工智能 ETF | 02807.HK Global X 中国机器人及人工智能 ETF | AI 软件 / 硬件 + 机器人产业链 |
| 光通信 | 03191.HK Global X 中国半导体 ETF | 03033.HK 南方东英恒生科技 ETF | 半导体 + 恒生科技代理；港股暂无纯光通信 ETF |
| 半导体 | 03191.HK Global X 中国半导体 ETF | 03076.HK 富邦台湾核心半导体 ETF | 中国 + 台湾半导体 |
| 贵金属 | 02840.HK SPDR 金 ETF | 03081.HK 价值黄金 ETF | 两只实物黄金 ETF |
| 美联储 / 利率 | 03436.HK 恒生美国国债 1-3 年 ETF | 03156.HK 博时美国国债 20+ 年 ETF | 短久期 + 长久期美国国债 |
| 地缘 | 02840.HK SPDR 金 ETF | 03156.HK 博时美国国债 20+ 年 ETF | 黄金 + 长久期美债避险观察 |
| 医药 | 02820.HK Global X 中国生物科技 ETF | 02841.HK Global X 中国医疗科技 ETF | 生物科技 + 医疗科技 |
| 智驾 | 02845.HK Global X 中国电动车及电池 ETF | 02807.HK Global X 中国机器人及人工智能 ETF | 电动车电池 + 机器人 / AI |
| 地产 | 03447.HK 南方东英亚太精选 REIT ETF | 03001.HK Premia 中国地产美元债 ETF | 亚太 REIT + 中国地产美元债 |
| 消费 | 02806.HK Global X 中国消费品牌 ETF | 03040.HK Global X MSCI 中国 ETF | 中国消费品牌 + MSCI 中国 |
| 航天 | 03140.HK 华夏港美人工智能 ETF | 02834.HK iShares 纳斯达克 100 ETF | AI + 纳指科技代理；港股暂无纯商业航天 ETF |

关键词会覆盖主题默认值：例如“原油”使用 03097.HK + 03175.HK 两只 WTI 原油期货 ETF；“数据中心”使用 02826.HK + 03140.HK；“白银 / 铂金 / 钯金”和“稀土”因没有现行纯主题港股 ETF，会返回明确标注的代理组合。

## 核验来源

目录按 2026-10-03 可获得的港交所基金资料库、上市文件与基金管理人产品页核对，优先保留 HKD 柜台代码：

- [港交所基金资料库：3436 恒生美国国债 1-3 年 ETF](https://ifp.hkex.com.hk/fund/BVB873)
- [港交所基金资料库：2807 中国机器人及人工智能 ETF](https://ifp.hkex.com.hk/fund/BPT301)、[3191 中国半导体 ETF](https://ifp.hkex.com.hk/fund/BPT300)、[2845 中国电动车及电池 ETF](https://ifp.hkex.com.hk/fund/BOW995)、[2826 中国云计算 ETF](https://ifp.hkex.com.hk/fund/BOJ918)
- [港交所上市文件：3140 华夏港美人工智能 ETF](https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0312/2026031200011.pdf)
- [港交所基金资料库：2806 中国消费品牌 ETF](https://ifp.hkex.com.hk/fund/BOW994)、[3040 MSCI 中国 ETF](https://ifp.hkex.com.hk/fund/BAR385)、[2834 iShares NASDAQ 100 ETF](https://ifp.hkex.com.hk/fund/BHG161)
- [港交所基金资料库：2800 盈富基金](https://ifp.hkex.com.hk/fund/AFG988)、[3195 恒生标普 500 ETF](https://ifp.hkex.com.hk/fund/BUP049)、[3081 价值黄金 ETF](https://ifp.hkex.com.hk/fund/ATY588)
- [Premia：3001 中国地产美元债 ETF](https://etfprod.premia-partners.com/etf/3001)
- [Global X：2820 中国生物科技 ETF](https://www.globalxetfs.com.hk/funds/china-biotech-etf/)、[2841 中国医疗科技 ETF](https://www.globalxetfs.com.hk/funds/china-medtech-etf/)、[2809 中国洁净能源 ETF](https://www.globalxetfs.com.hk/campaign/clean-energy/)
- [CSOP：3447 亚太精选 REIT ETF](https://www.csopasset.com/en/products/hk-crit)、[Samsung：3175 原油期货 ETF](https://www.samsungetfhk.com/en/product/3175/)
- [Solactive：2824 全球金矿精选 ETF 上市资料](https://www.solactive.com/articles/press-release-e-fund-launches-e-fund-hk-solactive-global-gold-miner-select-index-etf-tracking-the-solactive-global-gold-miner-select-index/)、[Global X：3014 铜矿 ETF 上市资料](https://www.prnewswire.com/apac/news-releases/global-x-launched-hong-kongs-first-copper-miners-etf-30149014-hk-302877721.html)
- [港交所：3156 美国国债 20+ 年 ETF 指数资料](https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2024/ce_SEHK_CT_020_2024_1.pdf)、[香港黄金 ETF 资料](https://www.hkex.com.hk/-/media/HKEX-Market/Products/Securities/Exchange-Traded-Products/Attachment/The-Rise-of-Gold-ETFs_vF.pdf)

产品仍可能更名、合并或终止上市；后续维护应先复核上述官方资料，再更新 `_HK_ETF_CATALOG`，不能只凭历史代码表继续输出。

## 验收

`HKEtfPairTest` 会检查：

- `_THEMES` 中每一个中英文关键词都能得到恰好两个不同 ETF；
- 代码统一符合 `NNNNN.HK`，并存在于核对后的目录；
- 每只产品都有非空对应指数；
- 原油、数据中心等精确覆盖生效；
- 热力图和板块机会均返回结构化 `etf_pair`；
- 旧 A 股个股组合不再出现在 AI 关键词解读中。
