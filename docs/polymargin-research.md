# PolyMargin 核心机制研报

> 标的：[PolyMargin.org](https://www.polymargin.org/)
> 日期：2026-04-21
> 目的：拆解 PolyMargin 的产品形态、核心机制、技术架构，并与 KPAX 已规划的抵押借贷产品做对比

---

## 一、摘要

**PolyMargin 不是杠杆产品，是借贷产品**——和此前分析的 Ultramarkets / Worm / Amplifi / LEVR.bet 都不同。它让用户**抵押 Polymarket 持仓借出 USDC**，而**不卖出预测仓位**。这几乎就是我们在 KPAX tech-plan 里规划的"抵押借贷"产品的**已上线版本**，所以是我们见过最直接相关的竞品。

**核心参数（从官网 + JS bundle 确认）**：
- 初始最高借款 **LTV 50%**
- 警告线 **70%**
- 强制清算线 **80%**
- 清算罚金 **仅 5%**（低于行业平均）
- **非托管**（用户资产 + 签名验证）
- 结算前自动监控到期时间（避免赛后结算）

---

## 二、产品定位

```
一句话: "Borrow USDC against your Polymarket positions without selling"
       用 Polymarket 持仓抵押借 USDC，不卖出仓位

Slogan: "Unlock liquidity from your Polymarket positions"
       释放你 Polymarket 仓位的流动性

形态: DeFi 借贷协议（类 Aave / Compound）
标的: Polymarket 的预测市场 Token 作为抵押品
```

**它 NOT 是什么**：
- ❌ 不是杠杆交易工具（不主动放大用户持仓）
- ❌ 不是衍生品（不创造新合成资产）
- ❌ 不帮用户在 PM 之外下注（没有 Hyperliquid 或跨市场整合）

**它 IS 什么**：
- ✅ 纯粹的抵押借贷（用户自愿决定钱的去处）
- ✅ 用户借出 USDC 后自由支配（可以离开 PM，也可以加仓 PM 别的市场）
- ✅ 为 PM 仓位提供流动性释放

---

## 三、核心运行机制

### 3.1 用户流程

```
Step 1: 连接钱包
  · Solana 钱包 → 用于身份认证（Auth）
  · Polygon MetaMask → 用于访问 Polymarket 持仓

Step 2: 选择抵押品
  · 从用户 Polymarket 持仓中挑选特定预测 token
  · MetaMask 签名证明所有权

Step 3: 借款
  · 最高借出仓位价值的 50%（LTV = 50%）
  · 即时到账 USDC

Step 4: 持仓期间
  · 仓位锁在协议合约（"safely held during loan period"）
  · 实时监控 LTV
    - 70% → 发送警告
    - 80% → 触发清算
  · 用户可随时还款取回抵押

Step 5: 到期前自动结算
  · 协议自动监控市场 resolution 时间
  · "Automatic monitoring of market expiry times to ensure loans are settled before resolution"
  · 类似 Ultramarkets 的"提前平仓"思路，规避 0/1 跳跃
```

### 3.2 LP（Lender Pool）侧

```
"Enable lenders to provide USDC liquidity and earn yield"

LP 模型:
  · 存入 USDC → 加入 Lender Pool
  · 资金借给 Polymargin 用户（由协议撮合）
  · 按借款需求获得利息收益

未公开的数据:
  · 具体 APY
  · 利率曲线
  · LP 提现规则
  · 是否有 LP 代币
```

---

## 四、关键参数表

| 参数 | 数值 | 说明 |
|------|------|------|
| 最高初始 LTV | **50%** | 保守，比 Aave（75-80%）低 |
| 警告线 | 70% LTV | 用户会收到通知 |
| 强制清算线 | 80% LTV | 触发 keeper 清算 |
| 清算罚金 | **5%** | 行业内最低档（Aave 5-10%，Compound 8%） |
| 抵押品 | Polymarket CTF token | ERC-1155 |
| 借款币种 | USDC | Polygon 上的 USDC |
| 贷款期限 | 到市场 resolution 为止 | 自动在 resolution 前还款或清算 |

---

## 五、技术架构

### 5.1 链与基础设施

```
Solana           Polygon
  ↓                ↓
用户身份         Polymarket 持仓
  ↓                ↓
  ╲              ╱
   ╲            ╱
    x402 payment rails
    跨链清算 + AI 调用
    ↓
Polymargin 协议
```

**关键点**：
- **双链架构**：Solana（auth）+ Polygon（仓位）
- **x402 结算层**：协议自述"runs on x402 — a pay-on-demand AI compute & payment engine"
- **非托管**："Your assets remain in your control. Collateral is held securely with full transparency."（但抵押期间仓位必须锁在合约）

### 5.2 x402 是什么（这是最独特的设计）

```
x402 官方定义:
"pay-on-demand AI compute & payment engine where each process 
consumes real compute, settled through micro-payment rails."

在 PolyMargin 场景中:
  · 每次 AI 分析（读取 on-chain 数据、sentiment、历史）
    → 通过 x402 消费算力
    → 微支付结算
  · Solana ↔ Polygon 跨链桥接通过 x402 payment rails 执行

"x402 transforms AI into a living economy"
"Every milestone fuels the next — funded, executed, and verified on x402."
```

**对 x402 的理解**：这是一种把 AI 推理作为"按量付费微服务"来设计的底层协议，PolyMargin 作为 x402 生态的应用层，用它来驱动**AI 驱动的协议操作**。具体是用来估值抵押品、预测风险、还是其他用途，官网没明示。

### 5.3 AI 功能

```
"Drop in a Polymarket link, token address, or market reference. 
PolyMargin's x402‑powered AI reads on‑chain data, sentiment, 
and historical context."

功能推测:
  1. 抵押品风险评分（AI 分析该市场的波动率）
  2. 动态 LTV 建议（给用户推荐合适的借款比例）
  3. 清算风险预警（提前识别可能触发 LTV 警告的市场）
  4. 市场分析（辅助用户决定是否借款以及借多少）
```

这是 PolyMargin 和纯 Aave 模式的最大区别——**AI 驱动的风险评估**。

### 5.4 安全设计

```
Non-Custodial Security:
  · 用户资产（钱包里的 USDC）始终在自己钱包
  · 抵押品进合约但用户仍然"持有"
  · MetaMask signature 证明所有权

Signature Verification:
  · "We verify ownership via signature"
  · "MetaMask signature proves wallet ownership"

Live Price Feeds:
  · 实时从 Polymarket 拉价格
  · 抵押品估值准确
  · 用于 LTV 动态计算

Market Expiry Tracking:
  · 自动监控所有抵押市场的 resolution 时间
  · 到期前强制还款/清算
```

---

## 六、与 KPAX 规划的抵押借贷产品对比

**PolyMargin 和 KPAX 抵押借贷设计几乎是同一个产品的不同实现**。详细对比：

| 维度 | PolyMargin | KPAX（规划） | 谁更好 |
|------|-----------|-------------|-------|
| 产品定位 | 借 USDC 做别的 | 借 USDC 买 PM 另一场 | 相同 |
| 最高 LTV | 50% | 规划 75% | KPAX 更激进（有风险） |
| 清算线 | 80% | 规划 85% | 基本持平 |
| 清算罚金 | 5% | 规划 1-2% | KPAX 更用户友好 |
| 抵押市场范围 | 所有 Polymarket 市场 | **仅体育类** | 互补（KPAX 更专精） |
| 赛前强平 | 自动到期监控，避免赛后结算 | kickoff-2h 强平 | KPAX 对体育更适配 |
| LP 池 | 单一 Lender Pool | 计划 kpaxUSD Vault | 类似 |
| 跨链 | Solana + Polygon | Polygon 单链 | PolyMargin 更复杂 |
| AI 集成 | x402 AI 风险分析 | **AI 深度比赛分析** | 不同角度（后者更深） |
| 代币 | 无 | 无 | 都好 |
| 合规风险 | 中等 | 中等 | 相同 |
| 用户接入 | Web App + 钱包 | **Chrome 插件** | KPAX 零摩擦 |
| X关注人数 | 96 |  |  |

### 关键差异总结

**PolyMargin 优于 KPAX 的地方**：
- ✅ 已上线，验证过产品可行性
- ✅ x402 AI 集成是新鲜的技术叙事
- ✅ 跨链设计更未来主义

**KPAX 优于 PolyMargin 的地方**：
- ✅ **浏览器插件**（用户在 Polymarket 上直接用，零转化摩擦）
- ✅ **体育垂直 AI 分析**（深度 > 广度）
- ✅ **kickoff-2h 专用强平规则**（体育场景最优）
- ✅ **清算罚金更低**（1-2% vs 5%）


---

## 七、PolyMargin 的优势和弱点评估

### 优势
1. **已上线运营**：有真实用户数据和反馈循环
2. **清晰的参数设计**：50% LTV / 70% 警告 / 80% 清算 / 5% 罚金，一目了然
3. **自动化到期处理**：规避了最大的 gap risk
4. **非托管 + 签名验证**：合规风险最低
5. **AI 集成先进**：x402 是新颖技术栈，技术故事性好
6. **无代币**：规避美国证券法问题

### 弱点
1. **通吃所有 PM 市场**：没有垂直深度（体育、政治、加密一视同仁）
2. **无专业内容**：用户拿到钱之后怎么做决策？PolyMargin 不管
3. **独立 Web App**：用户必须主动访问
4. **跨链架构复杂**：Solana + Polygon 双钱包对小白用户门槛高
5. **x402 依赖**：协议命运和 x402 生态绑定（x402 本身是小众技术）
6. **50% LTV 保守**：资本效率一般

---

## 八、对 KPAX 的战略启示

### 启示 1：我们规划的方向是对的 ✓

PolyMargin 上线并运营说明"抵押 PM 仓位借 USDC"是一个**被验证的产品方向**。我们之前的 tech-plan 不是空想，是**一个已被市场验证的赛道**。

### 启示 2：必须在 **3 个维度**上差异化

因为 PolyMargin 已占位，我们必须找到比它更强的地方：

```
1. 垂直化:
   · KPAX 只做体育（PolyMargin 是所有市场）
   · 配套 AI 分析帮用户做决策
   · 体育特有的风控（kickoff 时间、主力伤停、天气）

2. 分发:
   · KPAX 是 Chrome 插件（PolyMargin 是独立 App）
   · 用户在 Polymarket 页面内一键借款
   · 零转化摩擦

3. 用户体验:
   · 降低清算罚金（5% → 1-2%）
   · 赛前警告自动提醒（"2 小时后强平"）
   · AI 辅助决策（"基于我们的分析，你应该还款/加仓/清仓"）
```

### 启示 3：可抄的设计

1. **50% 初始 LTV + 70/80 分层警告** — 比我们之前的 75%/85% 更安全，建议采纳
2. **Market Expiry 自动监控** — 我们的 kickoff 强平是这个的体育版，思路一致
3. **非托管 + 签名验证** — 合规最优路径，照抄
4. **Lender Pool 两侧模型** — 简单清晰，直接抄

### 启示 4：不要抄的设计

1. ❌ **x402 依赖** — 绑定一个小众协议是风险，我们用自己的 AI 堆栈即可
2. ❌ **Solana 跨链架构** — 增加复杂度、用户门槛，KPAX 专注 Polygon 就好
3. ❌ **通吃所有市场** — 我们要垂直，不要和 PolyMargin 一样广谱

### 启示 5：时间紧迫性

PolyMargin 已经上线，意味着：
- **竞争窗口期已经开始**
- 用户心智一旦被占领，后来者会很难
- 但 PolyMargin **没做体育垂直 + 没做浏览器插件** → 这两点仍是 KPAX 的空白机会

**建议行动**：
1. 立即更新 tech-plan-centralized.md，采纳 PolyMargin 的关键参数（LTV 50%、警告 70%、清算 80%）
2. 优先推进抵押借贷产品（不是保险），因为已被验证
3. 注册 PolyMargin 账号实际体验 UX，用第一手数据更新本报告

---

## 九、更新后的竞品全景

```
                       预测市场增值层
                            ↓
        ┌───────────────────┼───────────────────┐
        │                   │                   │
      杠杆产品             借贷产品             分析产品
        │                   │                   │
   ┌────┴────┐           ┌──┴──┐              ┌─┴─┐
   │         │           │     │              │   │
 Ultra-    Worm      PolyMargin  (空位)       KPAX  (空位)
 markets            (已上线)    ← KPAX 可填补  (AI)
   │         │
 Amplifi   LEVR
           .bet
```

- **杠杆赛道**：4 家已占位，拥挤
- **借贷赛道**：仅 PolyMargin 1 家（且无体育垂直 + 无插件分发）
- **分析赛道**：KPAX 领先，无直接对手

**结论**：KPAX 应该以"**AI 分析为矛，借贷产品为盾**"的组合出击，避开杠杆红海，通过"插件分发 + 体育垂直"在借贷赛道撕开一个专属位置。

---

## 十、不确定信息（待验证）

以下信息官网和 JS bundle 都没明示，需要进一步确认：
- 具体的 **APY / 借款利率**
- **LP 提现规则**（是否有锁定期）
- **合约审计报告**
- **实际 TVL / 借款量**
- **团队背景 + 融资情况**
- **x402 是否为其唯一依赖，或可降级**
- **清算 keeper 是协议自己跑还是 permissionless**

建议：
1. 注册账号实测流程
2. 查找合约地址 + etherscan/polygonscan 验证代码
3. 关注 @polymargin__ 推特
4. 加入社区获取一手信息

---

## 附录：原始证据（从 JS bundle 提取）

```
产品定位:
- "Borrow USDC against your Polymarket positions without selling. Keep your market exposure..."
- "Lending Protocol for Prediction Markets"

LTV 参数:
- "Borrow up to 50% of your collateral value instantly. Repay anytime to unlock your positions."
- "Track your loan-to-value ratio in real time with alerts at 70% and liquidation at 80%."

清算机制:
- "Clear warning thresholds and fair liquidation process with only 5% penalty."
- "Automatic monitoring of market expiry times to ensure loans are settled before resolution."

架构:
- "Connect both Solana and Polygon wallets for seamless cross-chain lending."
- "Solana auth + MetaMask signature verification for Polygon."
- "Non-Custodial Security"

AI / x402:
- "PolyMargin's x402‑powered AI reads on‑chain data, sentiment, and historical context."
- "PolyMargin runs on x402 — a pay-on-demand AI compute & payment engine..."
- "Every milestone fuels the next — funded, executed, and verified on x402."

LP 侧:
- "Enable lenders to provide USDC liquidity and earn yield."
- "Lender Pools"

路线图:
- "Core lending protocol launched with Polymarket integration."
- "Expand to additional prediction markets and chains."
```

---

## 参考链接

- [PolyMargin 官网](https://www.polymargin.org/)
- [PolyMargin Docs (SPA，内容靠 JS 加载)](https://docs.polymargin.org/)
- [PolyMargin App](https://app.polymargin.org/)
- [PolyMargin X](https://x.com/polymargin__)
