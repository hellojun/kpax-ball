# 预测市场杠杆产品对比报告

> 对比对象：Ultramarkets、Worm、Amplifi、LEVR.bet
> 日期：2026-04-21
> 目的：在核心维度上横向比较四家产品，识别各自的技术路径和护城河

---

## 摘要

四家产品都叫"杠杆预测市场"，但底层架构差异巨大：

- **Ultramarkets** 和 **Amplifi** 选择"**寄生 Polymarket**"——借用 PM 的订单簿和流动性
- **Worm** 和 **LEVR.bet** 选择"**自营市场**"——自己做市、自己定赔率
- **Worm** 和 **LEVR.bet** 支持 **in-play 杠杆**，Ultramarkets 明确不支持，Amplifi 未明示
- 杠杆上限：LEVR.bet 最保守（5x），其余三家都到 10x
- 链选择：Polygon 系（UM + Amplifi）、Solana（Worm）、Avalanche（LEVR）

---

## 一、核心维度对比矩阵

| 维度 | Ultramarkets | Worm | Amplifi | LEVR.bet |
|------|-------------|------|---------|----------|
| **最高杠杆** | 10x | 10x（默认 3x） | 10x | **5x** |
| **底层市场** | Polymarket（寄生） | 自营 AMM | **Polymarket + Hyperliquid（双链）** | 自营 SBEX |
| **赔率来源** | Polymarket CLOB | 自营 AMM 曲线 | Polymarket CLOB | **12+ 传统体育博彩聚合** |
| **LP / 金库** | Vault + umUSD | AMM 内置 LP | 未公开（推测 under-collateralized pool） | **MVP Vault（GLP 式）** |
| **清算模型** | 事件前数天强制平仓 | Oracle + AMM + 用户 SL | adjustedLiquidationPrice（动态） | **Oracle 实时同步比赛事件** |
| **In-play 杠杆** | ❌ 明确不支持 | ✅ 支持 | ❓ 未公开 | ✅ **CLOB 支持** |
| **主链** | Polygon | Solana | Polygon + Hyperliquid | Avalanche C-Chain |
| **开放程度** | 邀请码 | 公开 | 邀请码 | 公开 |
| **X 关注** | 8168 | 1.1万 | 110 | 4.4万 |

---

## 二、杠杆使用方式对比

### Ultramarkets
```
10x 上限，任意精度（slider）
用户存 USDC 保证金 → Vault 借款 → 真实头寸在 Polymarket
事件 resolution 前数天自动全部平仓
```
**模型本质**：专业 Prime Broker。用户可以像在 CME 开期货那样"专业地"加杠杆。

### Worm
```
1-10x（URL 支持 2.5x 精度），UI 默认 3x
用户存入 + 选择杠杆 → AMM 内部开仓
实时平仓、可设 TP/SL
```
**模型本质**：散户 perp。交易感受接近 Binance 合约，但标的是体育事件。

### Amplifi
```
10x 上限，"Under-collateralized. Instant."
资金桥接到 Polymarket 或 Hyperliquid
宽 spread 时自动降杠杆（"Spread is wide — max leverage reduced to..."）
```
**模型本质**：跨市场 Prime Broker + 信用杠杆。最激进的设计。

### LEVR.bet
```
2-5x（最保守）
杠杆内置在"SBEX tokens"中（本身就是衍生品）
Beta 阶段不支持借款（全现金支付），未来会开放
```
**模型本质**：代币化的杠杆赛票。杠杆不是"借款"而是"赔付放大倍数"。

---

## 三、LP / 金库模型对比

### Ultramarkets: "0 directional exposure"
```
LP 存 USDC → 获得 umUSD（可转让）
收益:
  · 交易手续费分成
  · 借款利息
  · 清算费
  · 交易者净亏损份额

对冲机制: 真实头寸在 Polymarket，天然对冲
LP 风险: 低（号称无方向性敞口）
```

### Worm: AMM 本身即 LP
```
Bonding curve + 内置 LP 池
LP 风险: 承担所有杠杆用户的对手方风险（AMM 吃穿仓）
收益:
  · 2.5% 交易费的 50%
  · AMM spread
  · 清算收益

冷启动: bonding curve 初始 50/50 价格
单场市场规模较小（观察到 $11K 级别）
```

### Amplifi: 未公开（推测）
```
可能的 LP 模型:
  · Under-collateralized 需要大池子兜底
  · 可能跨链资产互认
  · 可能引入信用评分
LP 风险: 未知，但 "Under-collateralized" 意味着 LP 潜在风险更高
```

### LEVR.bet: MVP Vault（GMX 风格）
```
MVP Vault 作为统一对手方
  · 吸收所有输家的抵押品
  · 支付所有赢家的 $1/token
  · "Structured to avoid full risk exposure from any single game"
  
LP 风险: 明确限定单场敞口
代币模型:
  · $LEVR 基础代币
  · $veLEVR 锁仓（1-365 天线性解锁）
  · 10-80% 强制解锁惩罚
  · 回购 + 销毁机制
```

**最佳 LP 设计：LEVR.bet** — 明确的 "no single-game exposure" 规则 + 完整的代币经济。

**最激进：Amplifi** — Under-collateralized 意味着 LP 承担更高风险，但官方未披露细节。

---

## 四、赔率（Odds）来源对比

### Ultramarkets：直接用 Polymarket CLOB 价格
```
p_entry = p_Polymarket(t)
优点: 无套利、真实市场定价
缺点: 受 PM 流动性限制
```

### Worm：AMM 曲线内生
```
p_entry = f(pool_state)  # bonding curve 决定
优点: 可灵活定价，不依赖外部
缺点: 可能偏离真实概率，早期受 bonding curve 影响
```

### Amplifi：Polymarket CLOB + Hyperliquid 双源
```
PM 侧: p_entry = p_Polymarket
HL 侧: p_entry = p_Hyperliquid (perp mark price)
优点: 双市场套利机会
缺点: 套利路径复杂
```

### LEVR.bet：**12+ 传统体育博彩聚合**（独一无二）
```
p_entry = weighted_avg(DraftKings, FanDuel, Pinnacle, ...)
通过专门公式把传统赔率（如 +150 / -110）转成 token 价格

举例:
  underdog @ $0.42 → ~150% 潜在回报
  favorite @ $0.61 → ~66% 潜在回报

优点: 赔率更"传统"，体育迷熟悉；聚合多家降低偏差
缺点: 依赖中心化数据源，UMA 无法直接仲裁
```

**最有特色：LEVR.bet** — 用传统体育博彩的赔率而非 prediction market 的概率，**对体育用户来说心智无缝**。

---

## 五、清算价格公式对比

### Ultramarkets
```
标准 perp 公式（推测）:
  p_liq = p_entry × (k - 1 + MM) / k

特殊机制: 事件 resolution 前 N 天强制全部平仓
  → 清算主要由"时间触发"，价格触发是次要
```

### Worm
```
标准 perp 公式（更保守的 MM，推测 10%）:
  p_liq = p_entry × (k - 1 + MM) / k
  
AMM 滑点修正:
  p_liq_effective = p_liq × (1 - slippage × size/liquidity)
  
支持用户自设 Stop Loss
```

### Amplifi: adjustedLiquidationPrice
```
从 JS 代码中确认的机制:
  p_liq_base = standard perp formula
  adjustment = f(spread, depth, time, funding)
  adjustedLiquidationPrice = p_liq_base × (1 + adjustment)
  
UI 展示为 "worst-case estimate"
动态调整: spread 变大时清算价更保守
```

### LEVR.bet
```
机制最独特: oracle-driven liquidation engine
"synchronizes with live game events"
  → 清算不只是基于 token 价格
  → 而是基于实时比赛事件（进球、红牌）
  
举例:
  · 球队被罚红牌 → oracle 推送事件
  · 清算引擎立刻重估仓位
  · 触发清算或补充保证金要求
```

**最创新：LEVR.bet** — 唯一一个把**比赛事件本身**（不只是价格）纳入清算逻辑的产品。

**最保守：Ultramarkets** — 用时间窗口保护（提前数天平仓），规避了一切尖峰。

---

## 六、In-Play 杠杆支持

### Ultramarkets: ❌ 不支持
```
"Exit before the binary moment arrives"
事件前数天强制平仓 → 永远不暴露在 in-play 波动下
```

### Worm: ✅ 支持
```
UI 直接支持比赛进行中加杠杆
依赖 AMM 流动性 + 用户 SL 自保
Gap risk 由 LP 池吸收
```

### Amplifi: ❓ 未明示
```
代码里没有明确的"before kickoff"逻辑
但作为 Polymarket 杠杆层，PM 本身支持 in-play 交易
推测: 支持 in-play，但依赖动态 spread 调整降风险
```

### LEVR.bet: ✅ 支持（且是核心卖点）
```
"Full CLOB enables peer-to-peer trading during live games"
"Oracle-driven liquidation engine synchronizes with live game events"

Pre-game: epoch-based issuance + oracle liquidity injection
Live: CLOB 点对点交易

完整的 in-play 交易体验，类似传统体育 in-play betting
```

**竞争格局**：
- Ultramarkets 退出 in-play 赛道（认输）
- Worm 和 LEVR.bet 抢占 in-play 市场
- Amplifi 态度不明

---

## 七、费用结构对比

| 费用类型 | Ultramarkets | Worm | Amplifi | LEVR.bet |
|---------|-------------|------|---------|----------|
| 开仓费 | 未公开 | 2.5% | 未公开 | **"Hybrid，低 VIG"** |
| 借款利息 | 动态（类 Aave） | 未公开 | 未公开 | Beta 期无 |
| 清算费 | 未公开 | 未公开 | 未公开 | 未公开 |
| 提现费 | 未公开 | 未公开 | **有** | 未公开 |
| 资金费率 | ❌ 无（卖点） | 未明确 | 可能有 | 未明确 |
| 平台抽成 | 未公开 | **50% of 2.5%** | 未公开 | 未公开 |

**透明度最好：Worm**（公开 2.5% 费率和 50/50 分成）
**最模糊：Amplifi**（几乎全不公开）

---

## 八、核心差异化总结

### Ultramarkets 的杀手锏：**"Prime Broker + 提前平仓"**
- 定位清晰（服务 PM 专业 trader）
- 规避所有 in-play 风险（提前数天平仓）
- LP 号称"0 directional exposure"
- **适合：不急于 in-play、追求稳定的 LP 和专业 trader**

### Worm 的杀手锏：**"AMM + in-play + 散户 UX"**
- Solana 低延迟支持 in-play
- AMM 自动做市，流动性永远存在
- AI 辅助建市 + Twitter bot 分发
- **适合：追求刺激感的散户、创市者**

### Amplifi 的杀手锏：**"跨链 + Under-collateralized"**
- 同时覆盖 Polymarket 和 Hyperliquid
- 不足额抵押（更高资本效率）
- 动态杠杆（按 spread 调整）
- **适合：跨市场套利、高资本效率需求的专业 trader**

### LEVR.bet 的杀手锏：**"传统体育赔率 + 比赛事件清算 + 代币经济"**
- 赔率来自 12+ 传统博彩（体育迷心智无缝）
- Oracle 同步比赛事件（进球、红牌触发清算）
- 完整的 $LEVR/$veLEVR 代币经济
- **适合：传统体育博彩用户向 Web3 迁移**

---

## 九、关键数据对比（方便决策）

### 在 p_entry = $0.40 的多头仓位下的清算价

| 产品 | 杠杆 3x 清算价 | 杠杆 5x 清算价 | 杠杆 10x 清算价 |
|------|--------------|--------------|----------------|
| Ultramarkets (MM=5%) | $0.273 | $0.324 | $0.362 |
| Worm (MM=10% 推测) | $0.280 | $0.328 | $0.364 |
| Amplifi (带动态调整) | $0.27-0.30 | $0.32-0.35 | $0.36-0.39 |
| LEVR.bet | N/A（≤5x） | ~$0.33（估） | — |

### 在不同维度的"排名"

| 维度 | 冠军 | 亚军 | 季军 | 末位 |
|------|------|------|------|------|
| 杠杆灵活性 | Ultramarkets | Amplifi | Worm | LEVR.bet |
| 散户友好度 | Worm | LEVR.bet | Amplifi | Ultramarkets |
| LP 安全性 | LEVR.bet | Ultramarkets | Worm | Amplifi |
| In-play 体验 | LEVR.bet | Worm | Amplifi | Ultramarkets |
| 合规稳健性 | Ultramarkets | LEVR.bet | Worm | Amplifi |
| 资本效率 | **Amplifi** | Ultramarkets | Worm | LEVR.bet |
| 创新程度 | Amplifi | LEVR.bet | Worm | Ultramarkets |

---

## 十、对 KPAX 的启示

### 可抄的设计

1. **LEVR.bet 的"赔率来自传统博彩"**
   - 体育迷熟悉的 +150 / -110 表达
   - 对我们的体育垂直定位非常契合
   - 实施成本低（接入 Sportsradar、OddsAPI 即可）

2. **Amplifi 的"动态杠杆基于 spread"**
   - 流动性差时自动降杠杆
   - 直接可加到 KPAX 的借贷清算参数里

3. **Ultramarkets 的"提前平仓规避 gap risk"**
   - 和我们之前 tech-plan 的思路一致
   - 验证了这个路线是业内主流

4. **LEVR.bet 的"MVP Vault 限制单场敞口"**
   - GLP 式金库设计
   - 明确不让单场比赛 blow up LP

5. **Worm 的 "Twitter Bot 分发"**
   - @KpaxBall 机器人可以作为免摩擦流量入口

### 不要抄的设计

1. **Amplifi 的 Under-collateralized** — 我们做不了（需要大池子 + 信用评分）
2. **LEVR.bet 的 veLEVR 代币经济** — 合规风险高，不在 MVP 范围
3. **Worm 的 AMM 自营** — 冷启动成本高，我们寄生 PM 更合理
4. **Ultramarkets 的 10x 杠杆** — 体育散户不适合，2-3x 足够

### 独特护城河（KPAX 专有）

我们比四家任何一家都强的地方：
- ✅ **AI 深度分析**（四家都没有）
- ✅ **浏览器插件分发**（四家都需要独立 App）
- ✅ **体育垂直 + AI**（LEVR 体育但无 AI；其他都通吃）

---

## 十一、结论

**按"接近 KPAX 场景"的相似度排序**：

1. **LEVR.bet**（最相似）：体育专精 + 传统赔率 + MVP Vault
   → 最应该深入研究的竞品
   
2. **Ultramarkets**：PM 杠杆层 + 提前平仓
   → 技术路线参考
   
3. **Worm**：in-play + AI 辅助
   → UX 和分发参考
   
4. **Amplifi**：跨链 + Under-collateralized
   → 了解即可，技术太激进

**KPAX 的差异化战略**：
> "体育垂直的 AI Copilot + Polymarket 服务层，以 LEVR.bet 的赔率呈现 + Ultramarkets 的风控设计 + Worm 的分发创新，但不做它们的杠杆产品本体。"

---

## 十二、数据不确定性声明

由于四家文档访问受限，以下信息为推测：
- Ultramarkets 的具体 MM 值
- Worm 的具体 MM 和 AMM 曲线参数
- Amplifi 的完整费用结构和 LP 模型
- LEVR.bet 的具体清算公式

建议：注册各平台小额实测，用第一手数据更新本报告。

---

## 参考资料

- [Ultramarkets Introduction](https://docs.ultramarkets.xyz/introduction)
- [Worm Documentation](https://docs.worm.wtf)
- [Amplifi Homepage](https://amplifi.finance/)
- [LEVR.bet Official](https://levr.bet/)
- [LEVR.bet Explained - Dewhales Substack](https://dewhales.substack.com/p/levrbet-explained-onchain-betting)
- [Guide to LEVR.bet - Backpack Learn](https://learn.backpack.exchange/articles/guide-to-levr-bet)
- [LEVR on Avalanche - Avax Blog](https://www.avax.network/blog/levr-launches-the-first-leveraged-sports-betting-platform-on-avalanche)
