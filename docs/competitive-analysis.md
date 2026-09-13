# KPAX vs Ultramarkets vs Worm 竞品对比报告

> 日期：2026-04-21
> 目的：横向比较三个产品在预测市场杠杆赛道的设计选择，指导 KPAX 的产品定位和技术路径。

---

## 零、摘要（TL;DR）

| 产品 | 一句话定位 | 杠杆上限 | 底层市场 | 核心差异 |
|------|-----------|---------|---------|---------|
| **Ultramarkets** | Polymarket 的专业 Prime Broker | 10x | 寄生 PM | 赛前数天强制平仓，LP 无方向性敞口 |
| **Worm** | Solana 上的散户化预测市场 + 杠杆 | 10x（默认 3x） | 自营 AMM | 支持 in-play 杠杆，AI 辅助建市 |
| **KPAX** | Polymarket 的 AI 分析副驾驶 | 规划中（2-3x） | 寄生 PM | AI 深度分析 + 体育垂直 + 插件形态 |

**核心判断**：
1. Ultramarkets 和 KPAX 都是 Polymarket 的"增值层"，服务用户不同（专业 vs 体育爱好者）
2. Worm 是**独立的预测市场**（不依赖 PM），且有杠杆，是 KPAX 散户市场的直接威胁
3. 三者在技术栈、商业模式、合规风险上差异巨大

---

## 一、产品定位与商业模式

### Ultramarkets
```
定位: "Margin Layer for Prediction Markets"
       预测市场的保证金层 / Prime Broker
       
商业模式:
  · 交易手续费
  · 借款利息
  · 清算费
  · 交易者净亏损分成 (给 LP)

用户规模: 900+ Polymarket 交易者（公开数据）
融资: 未公开
```

### Worm
```
定位: "Permissionless prediction layer on Solana"
       去中心化的 Solana 预测市场 + 杠杆
       
商业模式:
  · 交易手续费 2.5%（创建者拿 50%，平台拿 50%）
  · 杠杆利息（具体费率未公开）
  · 清算收益

用户规模: 未公开（观察到单场市场 $11K 量级、124 浏览）
融资: $4.5M Pre-seed (6MV, Alliance, Solana Ventures, Borderless, Advancit)
```

### KPAX
```
定位 (现状): "AI-powered football analysis for Polymarket"
             体育 AI 分析的 Chrome 插件
             
定位 (规划): "AI Copilot for Sports Prediction Markets"
             带保险和借贷服务的体育投注 Copilot

商业模式 (规划):
  · KPAX Pro 订阅 (SaaS)
  · 保险产品保费
  · 借贷利息
  · 分析免费作为引流

用户规模: MVP 阶段
融资: 未公开
```

---

## 二、杠杆的使用方式

### Ultramarkets
```
开仓流程:
  1. 连接钱包 → 存入 USDC 作为保证金
  2. 选择 Polymarket 市场
  3. 选择方向（YES / NO）
  4. 选择杠杆倍数（1-10x）
  5. Vault 借出资本 → 在 Polymarket 执行真实头寸
  6. CTF token 锁在 Ultramarkets 合约
  
平仓流程:
  · 用户主动平仓（任何时候）
  · 触及强平线 → keeper 强平
  · 事件 resolution 前数天 → 自动全部平仓
  
杠杆挡位: 任意（0.1x 精度）
最低保证金: 未公开
```

### Worm
```
开仓流程:
  1. 连接 Solana 钱包
  2. 浏览市场（像 Polymarket 一样的界面）
  3. 选 YES/NO + 杠杆 slider (1-10x)
  4. 输入金额（小额快捷键 $1/$5/$10/$100）
  5. 可选设置 Take profit / Stop loss
  6. 一键确认 → 链上执行

平仓流程:
  · 用户主动平仓
  · 触及 TP/SL
  · 触及强平线 → keeper 强平
  · 事件 resolution → 自动结算

杠杆挡位: URL 参数支持 2.5x 精度（观察到的）
最低保证金: $1（极低）
```

### KPAX（规划）
```
开仓流程（抵押借贷路径）:
  1. 用户已在 Polymarket 持有 Token A
  2. 连接插件 → 识别持仓
  3. 抵押 Token A → 借出 USDC
  4. 借出 USDC 可买其他 Polymarket token
  5. Token A 的比赛开赛前归还

平仓流程:
  · 主动还款 → 释放抵押
  · 触及 LTV 阈值 → 强平
  · 赛前强制平仓（kickoff - 30min 到 2h 不等）

杠杆挡位: 计划 2x / 3x / 5x
最低保证金: 计划 $50
```

---

## 三、底层市场架构（最重要的技术选择）

| 维度 | Ultramarkets | Worm | KPAX |
|------|-------------|------|------|
| 市场来源 | **Polymarket（外部）** | **自营 AMM/bonding curve（内部）** | **Polymarket（外部）** |
| 流动性来源 | Polymarket CLOB 深度 | 自己的 LP 池 | Polymarket CLOB 深度 |
| 价格决定 | Polymarket 真实市场价 | AMM 内部曲线 | Polymarket 真实市场价 |
| 独立性 | 依赖 PM 存续 | **完全独立** | 依赖 PM 存续 |
| 覆盖范围 | PM 已有的市场 | **自己创建任意市场** | PM 已有的市场（子集） |
| 冷启动成本 | 无（PM 现成流动性） | 高（需要 bonding curve 启动） | 无 |

**关键启示**：
- 寄生 Polymarket（Ultramarkets、KPAX）= 流动性成本低 + 无套利约束 + 依赖 PM
- 自营市场（Worm）= 自主性高 + 灵活定价 + 冷启动难

---

## 四、运行原理详解

### Ultramarkets：Prime Broker Model
```
资金流:
  LP → Vault → umUSD 凭证
  Trader → Vault → 借款 → Polymarket 开仓
  Polymarket 头寸盈亏 → 回流 Trader
  手续费 + Trader 亏损 → 回流 LP

风险结构:
  · LP: "0 directional exposure"（号称无方向性敞口）
  · Vault: 做市商 + 借款人
  · Trader: 承担市场风险 + 利息
  · Polymarket: 中立交易场所

关键机制:
  · 真实头寸在 PM 上存在 → 天然对冲了 LP 的方向风险
  · 赛前数天平仓 → 规避 gap risk（他们自称）
```

### Worm：AMM + Bonding Curve
```
资金流:
  创建者 @ X 上标记 @WormPredict → AI 生成规则 → 市场创建
  Bonding curve 初始化（50/50 开盘）
  用户 → 买 YES/NO → 价格沿曲线移动
  用户 → 加杠杆 → 平台池子扩张仓位
  赛果 → UMA 结算 → 资金分配

风险结构:
  · LP（池子）: 承担所有杠杆用户的对手方风险
  · Trader: 承担市场 + 杠杆清算风险
  · 创建者: 拿手续费分成

关键机制:
  · AMM 曲线提供流动性 + 自带滑点保护
  · 支持 in-play 杠杆（这是其他产品不敢做的）
  · UMA 做最终 oracle（去中心化争议解决）
```

### KPAX（规划）：AI 驱动的 PM 服务层
```
当前资金流 (MVP):
  用户 → 插件 → 请求分析 → LLM 返回结果
  （无金融资金流动）

未来资金流 (规划):
  保险产品:
    用户 → 付保费 → KPAX Vault
    KPAX → 在 PM 买对冲头寸
    赛后 → 按结果赔付
    
  借贷产品:
    用户抵押 PM token → KPAX Vault 锁定
    KPAX 借出 USDC → 用户自由支配
    赛前 → 用户还款或强平

关键机制:
  · AI 深度分析作为核心差异化
  · 服务层，不重新发明预测市场
  · 专注体育垂直，深度 > 广度
```

---

## 五、风险控制机制对比

### 5.1 用户侧风险控制

| 工具 | Ultramarkets | Worm | KPAX |
|------|-------------|------|------|
| Take Profit | 未知 | ✅ 有 | 计划有 |
| Stop Loss | 未知 | ✅ 有 | 计划有 |
| 强平预警 | 未知 | 未知 | 计划有 |
| 部分平仓 | 未知 | 未知 | 计划有 |
| 最大亏损上限 | 保证金 | 保证金 | 抵押物 |

### 5.2 平台侧风险控制

**Ultramarkets**:
```
1. Resolution 前数天强制平仓 → 规避 gap risk
2. Safety Module（保险基金）
3. 持续健康度监控 → 动态强平
4. LP 池作为 systematic counterparty
5. 通过 Polymarket 真实头寸天然对冲
```

**Worm**:
```
1. 支持 in-play 杠杆 → 更激进，gap risk 由 LP 承担
2. AMM 曲线自带"市场冲击"保护（大单价格波动）
3. 小额上限 → 单个用户敞口有限（$1-$100 级别）
4. 默认杠杆较保守（3x 而非 10x）
5. Stop loss 用户自保
```

**KPAX（规划）**:
```
1. 仅赛前杠杆 → 规避 in-play gap risk
2. Kickoff 前强制平仓（根据联赛流动性分层：2h-48h）
3. LTV 分级（按 token 流动性）
4. 软清算（部分清算先，再全额清算）
5. Safety Module（保险基金）
6. 熔断机制（单 token 15min 跌 20% 停清算）
7. 白名单 token（只接受高流动性市场作抵押）
```

### 5.3 系统性风险（级联清算）

| 风险场景 | Ultramarkets | Worm | KPAX |
|---------|-------------|------|------|
| Gap risk（跳跃穿透） | 低（提前平仓）| **高**（in-play 暴露）| 低（仅赛前） |
| 级联清算 | 中 | 高 | 中 |
| 流动性死亡螺旋 | 低（PM 深度） | **高**（自营池有限） | 中 |
| Kickoff tsunami | 有（强制平仓集中）| 低（随时平仓） | 有（需分时段缓解） |
| 预言机操纵 | 中 | 中（UMA 缓解） | 中（TWAP 缓解） |

---

## 六、清算机制对比

### Ultramarkets
```
触发条件:
  1. LTV > 阈值（具体数值未公开）
  2. Resolution 前 N 天（"数天前"，可能 3-7 天）

执行方式:
  · Keeper 服务监控健康度
  · 在 Polymarket 上卖出 CTF token
  · 扣除债务 + 手续费
  · 剩余返还用户

特殊设计:
  · "Exit before the binary moment" 是核心设计哲学
  · 避免事件 resolution 时 $0/$1 跳跃
```

### Worm
```
触发条件:
  1. 用户设置的 Stop Loss
  2. LTV 超过清算线（具体阈值未公开）
  3. 事件最终结算（UMA）

执行方式:
  · 链上自动（Solana 低延迟）
  · 在 AMM 内直接平仓（无外部依赖）
  · AMM 曲线决定成交价

特殊设计:
  · 支持 in-play 清算
  · Solana 400ms 区块 → 快速响应
  · LP 池吸收所有穿仓风险
```

### KPAX（规划）
```
触发条件:
  1. LTV > 85%（清算线）
  2. 距开球 < 强平窗口时间
     · 英超/欧冠: kickoff - 2h
     · 次级联赛: kickoff - 12h
     · 小联赛: kickoff - 48h
  3. 抵押 token 流动性异常（订单簿骤降）

执行方式:
  · Keeper 服务通过 PM API 卖出 token
  · 软清算优先（部分清算，降低 LTV）
  · 最后才全额强平
  · 扣除债务 + 1% 强平费
  · 剩余返还用户

特殊设计:
  · 熔断机制 + 外部清算激励
  · 按联赛流动性分档设置参数
  · Safety Module 覆盖坏账
```

---

## 七、LP / 资金池模型

| 维度 | Ultramarkets | Worm | KPAX |
|------|-------------|------|------|
| LP 凭证 | umUSD（可转让） | 未公开（可能是 LP NFT 或代币） | 计划 kpaxUSD |
| 方向性敞口 | "0 directional"（号称）| **高**（承担杠杆对手方）| 中（借款风险，有抵押）|
| 收益来源 | 手续费 + 利息 + 清算 + 交易者亏损 | 手续费 + 利息 + AMM spread + 清算 | 手续费 + 利息 + 清算 |
| 冷启动机制 | 项目方自己注入 | Bonding curve + 自营种子 | 计划 bonding curve 激励 |
| 最小锁定期 | 未公开 | 未公开 | 计划 7 天 |
| APY 公开数据 | 未公开 | 未公开 | — |

**关键观察**：
- Ultramarkets 的 "0 directional exposure" 依赖于 PM 现货对冲（真实头寸在 PM 上）
- Worm 的 LP 承担了更多单方向风险（自营 AMM 没法完美对冲）
- KPAX 的抵押借贷 LP 是最接近传统 DeFi（Aave）的模型，风险最清晰

---

## 八、费用结构对比

### Ultramarkets
```
· 开仓费: 未公开
· 借款利息: 按 utilization 动态（类 Aave 曲线）
· 清算费: 未公开
· 资金费率 (funding rate): 无（这是他们的卖点）
· 分润比例: 未公开
```

### Worm
```
· 交易手续费: 2.5% per trade
  · 50% 给市场创建者
  · 50% 给平台
· 杠杆利息: 未公开
· 清算费: 未公开
· 资金费率: 未明确
```

### KPAX（规划）
```
· AI 分析: 免费（引流）
· KPAX Pro 订阅: $19.99/月
· 保险产品保费: 公平费 × (1 + 25% margin)
· 借贷利息: 按 utilization 动态（类 Aave）
· 清算费: 1-2% 抵押物
· 平台抽成: 10-15%
```

---

## 九、预言机与结算

| 维度 | Ultramarkets | Worm | KPAX |
|------|-------------|------|------|
| 价格预言机 | Polymarket CLOB（直接读取）| 自营 AMM 内部价格 | Polymarket CLOB TWAP |
| 结算预言机 | Polymarket resolution（UMA）| **UMA Optimistic Oracle** | Polymarket resolution（间接用 UMA） |
| 争议解决 | 跟随 PM | UMA token 投票 | 跟随 PM |
| 预言机操纵风险 | 低（依赖 PM）| 中（UMA 机制成熟但仍有风险）| 低（依赖 PM + TWAP） |
| 赛事时间数据 | 未公开 | 未公开 | 计划 Sportsradar API |

---

## 十、分发与用户获取

| 渠道 | Ultramarkets | Worm | KPAX |
|------|-------------|------|------|
| 独立 Web App | ✅ | ✅ | ❌ |
| 浏览器插件 | ❌ | ❌ | ✅ **核心** |
| Mobile App | 未知 | 未知 | 未规划 |
| Twitter Bot | ❌ | ✅ **@WormPredict** | 可学习 |
| Discord / 社群 | ✅ | ✅ | 可建 |
| SEO / 内容营销 | 未知 | 未知 | 可发力 |
| 合作伙伴 | Polymarket 官方未认可 | Solana Ventures 支持 | — |

**分发策略分析**：
- Ultramarkets：需要用户离开 Polymarket → 高转化成本
- Worm：用户在自己平台，+ Twitter bot 免摩擦引流
- KPAX：**浏览器插件 = 用户在 Polymarket 使用 KPAX**，零转化摩擦（巨大优势）

---

## 十一、AI 能力对比

| AI 能力 | Ultramarkets | Worm | KPAX |
|---------|-------------|------|------|
| 深度赛事分析 | ❌ | ❌ | ✅ **核心能力** |
| 定价模型 | 未公开 | 未知 | AI 参与定价 |
| 规则生成 | ❌ | ✅（@WormPredict）| 未规划 |
| 用户投注建议 | ❌ | ❌ | ✅ 规划中 |
| 多模型集成 | ❌ | ❌ | ✅ LiteLLM |
| 实时赛事解读 | ❌ | ❌ | ✅ MVP 已有 |

**结论**：**AI 是 KPAX 最显著的差异化**。Ultramarkets 完全不碰 AI，Worm 用 AI 做的是建市辅助（边缘功能），只有 KPAX 把 AI 作为产品核心。

---

## 十二、合规与监管风险

### Ultramarkets
```
主要风险:
  · 10x 杠杆对美国 SEC 高度敏感
  · 可能被认定为衍生品
  · umUSD 代币或被认定为证券
  · 无明确地域限制声明

缓解:
  · 寄生 Polymarket（依附已有合规）
  · 仅限 Polygon，规避美国直接监管
```

### Worm
```
主要风险:
  · 预测市场在美国合规灰色
  · 杠杆 + 预测市场双重敏感
  · UMA 争议结算可能被质疑

缓解:
  · 明确不发代币（规避证券法）
  · Solana 部署（不同于 Polymarket 的 Polygon）
  · 去中心化建市（责任分散）
```

### KPAX
```
主要风险:
  · 金融产品（保险 / 借贷）对用户身份合规要求高
  · 跨境数据合规（GDPR）
  · 浏览器插件可能涉及 Chrome Web Store 政策

缓解:
  · SaaS 订阅模式风险最低
  · 保险 / 借贷不直接让用户杠杆化（间接）
  · AI 分析不涉及资金流，合规最轻
  · 隐私政策已就位
```

**综合**：KPAX 合规风险最低（走 SaaS 为主路线）；Worm 因不发币规避了最大陷阱；Ultramarkets 最高风险。

---

## 十三、技术栈对比

| 组件 | Ultramarkets | Worm | KPAX |
|------|-------------|------|------|
| 区块链 | Polygon | Solana | Polygon |
| 智能合约语言 | Solidity | Rust (Anchor) | Solidity（规划） |
| 账本系统 | 自研 + Blnk | 未公开 | PostgreSQL |
| 前端 | React（推测） | React（推测） | **React + Chrome Extension** |
| 钱包集成 | MetaMask、WalletConnect | Phantom、Solflare | **Privy**（规划） |
| 交易引擎 | Polymarket CLOB | 自营 AMM | Polymarket CLOB |
| 后端 | 未公开 | 未公开 | **FastAPI + Python** |
| AI 模型 | 无 | GPT/Claude（推测，用于规则） | **LiteLLM 多模型调度** |

---

## 十四、竞争矩阵

### 谁和谁直接竞争

```
Polymarket 用户池
  ↓
  ├─ Ultramarkets ← 偏专业 trader（10x 杠杆）
  └─ KPAX        ← 偏体育爱好者（分析 + 金融服务）
     
独立 Solana 用户池
  └─ Worm        ← 散户 + 休闲娱乐（3x 杠杆 + 小额）

交叉竞争:
  · Worm 的体育散户可能分流 KPAX 的潜在用户
  · Ultramarkets 未来如果下沉到散户，会和 KPAX 正面冲突
  · Polymarket 如果自己推出杠杆，3 家都受冲击
```

### 各自护城河

**Ultramarkets**:
- 先发优势（已有 900+ trader）
- "Prime Broker" 专业定位
- 10x 高杠杆吸引专业玩家
- Blnk 等基础设施合作

**Worm**:
- Solana 原生（交易成本低）
- 自营市场（不受 PM 制约）
- AI 建市工具（差异化）
- Twitter Bot 分发（独特）
- $4.5M 融资（品牌背书）

**KPAX**:
- **AI 深度分析**（核心壁垒）
- **体育垂直专精**（聚焦）
- **浏览器插件**（零摩擦）
- 完整用户旅程（分析 → 决策 → 下注 → 风险管理）

### 各自劣势

**Ultramarkets**:
- UX 复杂（用户要学新平台）
- 无 AI，纯工具
- 高杠杆对散户不友好
- 依赖 Polymarket

**Worm**:
- 流动性需要自己冷启动
- Solana 用户池 < Polygon + Ethereum
- In-play 杠杆的系统性风险
- 无差异化分析内容

**KPAX**:
- 金融产品未上线（纸面规划）
- LP 冷启动困难
- 杠杆产品数学上受限（寄生 PM）
- 团队资源有限

---

## 十五、对 KPAX 的战略启示

### 启示 1：不要正面硬刚杠杆赛道

Ultramarkets 的 Prime Broker 定位已经占住"专业 trader 的 PM 杠杆"。Worm 的 Solana AMM 已经占住"散户 in-play 杠杆"。

**KPAX 的杠杆产品如果只是"另一个加杠杆的工具"，没有特别的理由让用户选我们。**

### 启示 2：AI 是唯一真护城河

```
Ultramarkets: 0 AI
Worm: AI 辅助建市（非核心）
KPAX: AI 是产品核心
```

**战略重心应该放在 AI 分析的深度和准确性上，其他产品（保险、借贷）作为 AI 生态的变现手段。**

### 启示 3：浏览器插件是分发核武器

Ultramarkets 和 Worm 都需要用户主动访问独立 App。KPAX 在 Polymarket 浏览时自动出现——**转化摩擦为 0**。

**应该把插件体验打磨到极致，而不是着急做杠杆产品。**

### 启示 4：学习 Worm 的几个设计

可以借鉴：
- ✅ Twitter Bot 引流（@KpaxBall）
- ✅ Stop Loss / Take Profit 工具
- ✅ 小额快捷按钮（$1/$5/$10/$100）UX
- ✅ 极简主义前端

不建议：
- ❌ In-play 杠杆（风险太大）
- ❌ 自营 AMM（冷启动成本高）
- ❌ Solana 部署（分散注意力）

### 启示 5：学习 Ultramarkets 的几个设计

可以借鉴：
- ✅ "Prime Broker" 类的专业叙事（提升品牌感）
- ✅ umUSD 的 Vault Token 模型
- ✅ Safety Module 独立设计
- ✅ LP "0 directional exposure" 话术

不建议：
- ❌ 10x 高杠杆（散户不适合）
- ❌ "赛前数天平仓"（我们 kickoff-2h 更符合体育场景）

### 启示 6：重新审视产品优先级

基于这次对比，建议产品优先级调整为：

```
Phase 1 (立即): AI 分析 + Chrome 插件 + KPAX Pro 订阅
  · 最核心的 AI 差异化
  · 合规风险最低
  · 快速变现
  · 用户教育

Phase 2 (6 个月后): 保险产品
  · 用户画像更清晰
  · LP 资金来源验证后
  · 数学模型最稳

Phase 3 (12 个月后): 抵押借贷
  · 用户规模足够后
  · 参考 Worm 和 Ultramarkets 的实际运营数据
  · 借鉴行业 best practice

Phase 4 (18 个月后): 如果市场需要，考虑自营杠杆产品
  · 只有在确认 KPAX 有足够 AI 优势时
  · 否则保持"PM 服务层"定位
```

---

## 十六、需要验证的不确定信息

以下信息需要进一步调研（多数因对方文档受限）：

| 问题 | Ultramarkets | Worm |
|------|-------------|------|
| 确切的清算阈值 | ❓ | ❓ |
| LP 的实际 APY | ❓ | ❓ |
| 借款利率曲线 | ❓ | ❓ |
| 保险基金规模 | ❓ | ❓ |
| DAU / MAU | ❓ | ❓ |
| 历史坏账事件 | ❓ | ❓ |
| 合约审计报告 | ❓ | ❓ |
| 地域限制 | ❓ | ❓ |

**建议行动**：
1. 注册两个产品的账户，实际下小额测试
2. 研究他们的智能合约代码（都是链上）
3. 关注他们的 Discord / X 社区
4. 尝试联系团队获取合作或文档权限

---

## 附录：核心参考资料

- [Ultramarkets Introduction](https://docs.ultramarkets.xyz/introduction)
- [Ultramarkets Official Site](https://www.ultramarkets.xyz/)
- [Worm Documentation](https://docs.worm.wtf)
- [Worm Official Site](https://www.worm.wtf/)
- [BSC News: Worm.wtf Explained](https://bsc.news/post/worm-wtf-predictions-solana-explained)
- [Blnk Finance: Ultramarkets Case Study](https://www.blnkfinance.com/customers/ultramarkets)
- [Hyperliquid Vaults (参考 HLP 设计)](https://arx.trade/blog/hyperliquid-vaults-explained/)

---

> 本报告基于 2026-04-21 的公开信息。因对方文档多受访问限制，部分技术细节为基于公开材料的推测。建议在做重大战略决策前进行更深入的实地调研。
