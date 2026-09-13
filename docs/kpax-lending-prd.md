# KPAX Lending 产品需求文档 (PRD)

> **状态**：Draft v0.2
> **作者**：PM (KPAX)
> **日期**：2026-04-25
> **依赖**：`docs/polymargin-research.md`（PolyMargin 研报）、`docs/tech-plan-centralized.md`、`docs/competitive-analysis.md`

---

## 0. 摘要（一分钟读完）

**产品**：KPAX Lending —— 在 Polymarket 上对足球预测仓位的抵押借贷服务。

**用户故事**：
> 我在 Polymarket 押了 $500 的"Man City 夺冠"，但下周就是开赛日，我想用这仓位的钱去押另一场也很看好的比赛，同时不想卖出 Man City。KPAX 一键让我抵押借出 USDC，由 AI 告诉我"按历史波动，这个标的 50% LTV 是合理的、开赛前 2 小时系统自动强平"。

**价值主张**：
```
AI + 插件 + 体育垂直   = 比 PolyMargin 更贴合体育用户的借贷体验
```

**MVP 3 条差异化**：
1. **Chrome 插件原生接入**：用户在 Polymarket 看比赛的同时就能借贷，不跳转外部 Web App
2. **AI 风控建议**：根据球队波动性、开赛时间、联赛流动性，给出"建议借款额 / 合理 LTV"
3. **体育专用清算规则**：按比赛流动性分层 LTV（英超 / 欧冠 vs 小联赛不同参数），Kickoff 前自动平仓避免 in-play gap

**商业模式**：利率差价（LP 收息 + 借款利息之差的 20% 抽成）、清算费分成。

**开发预估**：12 周（Phase 1 MVP 6 周 + 风控压测 3 周 + 公测 3 周）

---

## 1. 背景与市场机会

### 1.1 已验证的赛道
PolyMargin 在 2026 年已经上线同类产品，证明"抵押 PM 仓位借 USDC"是真实需求。用户痛点是共通的：
- **不想卖出仓位又想要流动性**
- **发现新机会但资金已占用**
- **做组合对冲但没有现金**

### 1.2 PolyMargin 留下的空位
基于研报，PolyMargin 的 3 个弱点正好是 KPAX 的机会：

| PolyMargin 的弱点 | KPAX 的天然优势 |
|------------------|----------------|
| 通吃所有 PM 市场，没有垂直深度 | **专注体育**，知道哪支球队、哪个联赛风险多高 |
| 独立 Web App，用户必须主动访问 | **浏览器插件**，在 Polymarket 页面内一键触达 |
| 无专业内容，用户借钱后自己决策 | **AI 分析** + 借贷 = 完整决策链路 |

### 1.3 与 KPAX 现有产品的关系
```
             KPAX 用户旅程
                    ↓
  AI 分析        ← Pro 订阅 (SaaS 变现)
  比赛识别       ← 免费引流
       ↓
   决策辅助
       ↓
   资金管理（缺口）  ← Lending 填补于此
       ↓
   保险/杠杆（Phase 3+）
```

Lending 是**把用户从"看分析"变成"用钱行动"**的关键一步，是 KPAX 从 SaaS 升级为金融服务的跳板。

---

## 2. 竞品对比速览

| 维度 | PolyMargin | KPAX Lending |
|------|-----------|--------------|
| 最高 LTV | 50% | **按联赛分层**（顶流 60%，次级 50%，小众 40%）|
| 警告阈值 | 70% | 70% |
| 清算阈值 | 80% | 80% |
| 清算罚金 | 5% | **2%**（低档吸引用户，差额来自 AI 风控带来的坏账率下降）|
| 抵押范围 | 所有 PM 市场 | **只接体育 token 白名单** |
| 入口 | 独立 Web App + 钱包 | **Chrome 插件 Side Panel** |
| AI 参与 | x402 风险评分（不深入） | **深度 AI 分析 + 借款推荐 + 实时预警** |
| 自动到期处理 | 到 resolution 前平仓 | **Kickoff 前 2 小时平仓** |
| 跨链 | Solana + Polygon | 仅 Polygon（简化 MVP） |
| 代币 | 无 | 无 |

---

## 3. 目标用户

### 3.1 用户画像

**主画像：Alex — 深度体育粉 + Polymarket 老玩家**
- 年龄 28-40 岁，男性居多
- PM 余额 $500-$10,000
- 每周在 PM 下注 $100-$500
- 看英超 / 欧冠 / 世界杯，可能看本土联赛
- 有基础 DeFi 认知（知道 USDC、会用 MetaMask）
- 已装 KPAX 做 AI 分析
- **Polymarket 登录方式：MetaMask / WalletConnect 等外部钱包**（占目标人群约 60%）

**次画像：Bobby — 专业投注者 / 机构小户**
- PM 余额 $10,000+
- 会做多场比赛的组合投注
- 需要频繁资金流转
- 可能同时在 Ultramarkets / Worm 下注
- 对利率敏感，愿意比较不同平台
- **Polymarket 登录方式：外部钱包（基本 100%）**

**第三画像：Casey — Polymarket 邮箱/Google 用户（Magic-link）**
- 用社交账号登录，钱包对他来说是黑盒
- 资产存量较小（< $500），但 PR 量大（占目标人群约 40%）
- DeFi 经验较浅，但能跟着引导一步步操作
- **登录路径：在 Polymarket 上点 Settings → Export Private Key 把 Magic 派生 EOA 私钥导出 → 导入 MetaMask 后等同 Alex / Bobby 流程**
- 一次性导出操作完成后，使用体验和外部钱包用户完全相同

### 3.2 关键用户痛点

1. **"我的仓位被锁死"**：PM token 要等 resolution 才能兑现，期间机会成本大
2. **"我想押新的但钱在老仓位"**：卖出再买入会吃手续费 + 价差 + 滑点
3. **"借贷平台不懂体育"**：PolyMargin 对一场英超和一场小联赛一视同仁，LTV 限死 50%
4. **"我不想学新平台"**：专用 Web App 要下载、连钱包、搞明白界面
5. **"我用 Google 登录的 Polymarket，连钱包都没碰过"**（仅 Casey 有此痛点）：KPAX 提供导出私钥引导，一次性把门槛跨过去

### 3.3 反画像（不服务的用户）
- 只押非体育市场（政治、加密）的用户 → 去 PolyMargin
- 需要 10x 杠杆的专业 perp trader → 去 Ultramarkets
- 只做 in-play 投注的用户 → 我们不支持 in-play 抵押
- **从未在 Polymarket 上交易过的新用户**：没有 proxy 也就没有抵押物，不在 Lending 范围（仍然可以用 KPAX 的 AI 分析功能）

---

## 4. 核心价值主张

### 4.1 一句话
**"把你的 Polymarket 球赛仓位变成可以花的 USDC，AI 帮你算风险。"**

### 4.2 三条卖点

**卖点 1 · AI 给出借款建议**
> "根据历史波动率 + 距开赛时间，我们建议你借 $230（53% LTV），低于该值强平风险仅 4%"

**卖点 2 · 在 Polymarket 页面内直接借**
> 打开任意 PM 球赛页面 → 侧边栏插件 → 识别到你持仓 → 一键开抵押，不离开 PM

**卖点 3 · 为体育量身定制的风控**
> 英超准许 60% LTV，中甲只给 40%；大牌球队伤停消息实时提醒；Kickoff 前 2 小时自动平仓

---

## 5. 产品范围

### 5.1 MVP（Phase 1）必做

| 模块 | 描述 |
|------|------|
| **登录 + 钱包检测** | 用户用 MetaMask 登录 KPAX；KPAX 用其 EOA 自动派生 Polymarket Safe / Magic proxy 地址，链上探测哪个有代码 |
| **Magic 引导（条件触发）** | 若派生出 Magic 类型 proxy 但 Safe 没有，弹引导让用户去 Polymarket 导出私钥再回来重连接 |
| 持仓导入 | 用 proxy 地址查 Polymarket Data API + 链上 `CTF.balanceOf(proxy, tokenId)`，识别用户的体育 token 持仓 |
| **Terms of Service** | 首次借款前弹出 ToS，用户签署后方可继续（D8） |
| **proxy 一次性授权** | 用户用 EOA 签 Safe `execTransaction` 调用 `CTF.setApprovalForAll(KpaxVault, true)`；签过一次后所有借款复用 |
| 抵押借款 | 用户选仓位 → AI 推荐借款额 → 确认 → Privy/MetaMask 签名 `openLoan(proxy, tokenId, ...)` → Vault 从 proxy 拉取 CTF + USDC 打到 EOA。抵押物完全锁定（D2） |
| AI 推荐 + 用户覆盖 | LTV 默认采纳 AI 建议；用户可上调至联赛 Tier 上限，超过建议值显示黄色警告（D5） |
| 仓位看板 | 显示当前所有抵押仓位 + LTV 实时监控 + 距开赛倒计时 |
| 还款（全额/部分）| 从 EOA 钱包 USDC 还款（D3）；任意金额均可（D4）；还清自动把 CTF 退回 proxy |
| 清算 Keeper | KPAX Keeper 独立触发（D6），LTV > 80% 或 kickoff-2h 自动强平。Keeper 直接把 CTF 从 Vault 卖到 Polymarket Exchange |
| LP 池（KPAX 自运营）| KPAX Treasury 注资提供 USDC，不开放外部 LP（D1） |
| AI 风控引擎 | 基于历史数据 + 实时波动给出 LTV 建议 |
| 余额/余额刷新 | 侧边栏和个人页显示 USDC 余额 + 借款状态 |

### 5.2 MVP 明确**不做**（砍到 Phase 2/3）

| 功能 | 延后原因 |
|------|---------|
| 外部 LP 开放 | 需要更成熟的风控模型 + 审计 |
| 多 token 组合抵押 | UX 复杂，先做单 token 验证 |
| 动态利率（按 utilization） | MVP 固定利率简化 |
| 非体育市场抵押 | 我们的 AI 和垂直专业性在体育，其他类型等数据积累 |
| In-play 借款 | gap risk 大，风控模型不成熟 |
| 追加保证金（Top-up） | 基础版让用户直接再开一笔 |
| 跨链（Solana）| Polygon 已足够覆盖 Polymarket 用户 |
| Mobile App | 插件优先 |
| 代币 $KPAX | 合规风险，暂不做 |

---

## 6. 核心用户流程

### 6.0 钱包架构前提

KPAX Lending 的抵押物在用户的 **Polymarket proxy 钱包**里，不在用户登录 KPAX 用的 EOA 里。
KPAX 后端通过 EOA 自动派生 Polymarket proxy 地址（链上 CREATE2 反推），并探测它属于哪种类型：

| 类型 | Polymarket 登录方式 | proxy 工厂 | 派生公式 | KPAX 处理 |
|------|------------------|----------|---------|----------|
| Safe | MetaMask / WalletConnect | `0xaacFeEa0...` | `salt = keccak256(abi.encode(eoa))` | 直接走 6.1 流程 |
| Magic-link | 邮箱 / Google / Apple | `0xaB45c5A4...` | `salt = keccak256(packed(eoa))` | 走 6.1.5 引导 |
| 都没 | 用户从未在 PM 交易过 | — | — | 提示"先去 Polymarket 买一份仓位" |

完整链上常量见技术文档；MVP 检测算法：
1. 算 Safe 派生地址 → `eth_getCode` 探测
2. 算 Magic 派生地址 → `eth_getCode` 探测
3. 哪个有代码就用哪个

### 6.1 Borrower 流程（用户借款）—— 主路径（Safe 用户）

```
┌─────────────────────────────────────────────────────────────────┐
│  用户用 MetaMask 登录 KPAX（Privy 检测到外部钱包，跳过嵌入流程）    │
│  KPAX 后端用 EOA 派生 Safe proxy 地址 → 探测到有代码 → 路径 OK    │
│                                                                    │
│  用户在 Polymarket 比赛页                                          │
│                                                                    │
│  Side Panel 底部出现入口：                                          │
│  ┌────────────────────────────────────┐                          │
│  │ 💰 You hold $500 in this market    │                          │
│  │    Borrow up to $250 with KPAX →   │                          │
│  └────────────────────────────────────┘                          │
│                                                                    │
│  点击 →                                                            │
│                                                                    │
├─────────────────────────────────────────────────────────────────┤
│  首次借款前 · Terms of Service 弹窗（D8）                            │
│                                                                    │
│  ┌──────────────────────────────────────────────┐                 │
│  │ KPAX Lending · 服务条款                       │                 │
│  │                                                │                 │
│  │ - 借贷工具，不构成投资建议                       │                 │
│  │ - 资产由智能合约托管，KPAX 非托管方              │                 │
│  │ - 存在合约漏洞 / 清算滑点 / 价格波动风险          │                 │
│  │ - 你对借款决定负全部责任                         │                 │
│  │ [ 阅读完整条款 ]                               │                 │
│  │                                                │                 │
│  │ [x] I have read and agree                     │                 │
│  │ [ Continue ]  [ Cancel ]                      │                 │
│  └──────────────────────────────────────────────┘                 │
│                                                                    │
│  签署一次后在 users 表记 ToS 版本号，下次免弹；ToS 升版重弹。           │
│                                                                    │
├─────────────────────────────────────────────────────────────────┤
│  抵押设置页                                                         │
│                                                                    │
│  Token A: Man City 夺冠                                           │
│  持有量: 833 shares (@ $0.60 = $500)                              │
│  距开赛: 6 天 14 小时                                              │
│                                                                    │
│  ┌─────── AI 建议 ───────┐                                        │
│  │ 📊 建议借款: $250 (50% LTV)                                  │
│  │ 风险评分: 低 (2/10)                                           │
│  │ 强平风险概率: 3.2%                                            │
│  │                                                                │
│  │ 联赛流动性: 高 (英超)                                          │
│  │ 最近 7 天价格波动: ±4%                                         │
│  └────────────────────────────────────┘                          │
│                                                                    │
│  借款金额：                                                         │
│    [────●────────────────]  $250 of max $300                     │
│                              (50% LTV, AI 建议)                    │
│                                                                    │
│    若滑块超过 AI 建议（> 50% LTV），展示:                             │
│    ┌──────────────────────────────────────┐                       │
│    │ ⚠️ 超过 AI 建议的 50% LTV，强平风险显  │                       │
│    │    著上升。当前 60% 仍在该联赛上限内，│                       │
│    │    可继续，但请自行评估。              │                       │
│    └──────────────────────────────────────┘                       │
│    硬上限为联赛 Tier 的 LTV 上限（如 Tier 1 = 60%）                 │
│                                                                    │
│  费用预览：                                                         │
│    · 年化利率: 12.5%                                              │
│    · 持有 6 天成本: $0.51                                         │
│    · 开仓费: 免费                                                 │
│                                                                    │
│  强平规则：                                                         │
│    · LTV > 80% 立刻强平                                           │
│    · 开赛前 2 小时（约 4 天 14 小时后）强制平仓                     │
│                                                                    │
│             [ 确认借款 $250 ]                                      │
├─────────────────────────────────────────────────────────────────┤
│  签名确认（2 步：proxy 授权 + 借款执行；首次需两签）                  │
│                                                                    │
│  Step 1 · 仅首次：proxy → KPAX Vault 授权 (一次性)                  │
│    内容: Safe.execTransaction(                                     │
│              to: CTF合约,                                           │
│              data: setApprovalForAll(KpaxVault, true))              │
│    用户签：MetaMask 弹窗显示 Safe tx → 签名 → 广播                   │
│    完成后所有借款共用此授权，永久有效（除非用户撤销）                   │
│                                                                    │
│  Step 2 · LendingVault.openLoan(proxy, tokenId, 833 shares, $250) │
│    合约原子执行：                                                    │
│      1. ctf.safeTransferFrom(proxy, vault, 833 shares)              │
│         🔒 把 833 股从 proxy 转入 LendingVault 托管                  │
│      2. usdc.transfer(eoa, $250)                                    │
│         💵 从 LP 池打 $250 到用户 EOA 钱包                           │
│      3. 记录借款 { borrower=eoa, collateralSource=proxy, ... }      │
│                                                                    │
│  Step 1 已签过的用户后续借款只签 Step 2，1 次 click → 完成            │
│      ↓                                                            │
│  链上执行 (5-10s)                                                  │
│                                                                    │
│  ⚠️ 关键点：抵押物从此锁在 LendingVault 合约里，直到你还款释放         │
│  或被强平。KPAX 团队无法单方面挪用，合约规则受审计约束。                 │
├─────────────────────────────────────────────────────────────────┤
│  完成页                                                            │
│                                                                    │
│  ✅ 借款成功                                                        │
│  $250 USDC 已到达您的 EOA 钱包                                     │
│  833 股 Token A 已锁入 KPAX LendingVault                          │
│  合约地址: 0xABCD...1234  [ 在 Polygonscan 查看 ]                  │
│                                                                    │
│  [ 查看仓位 ]  [ 再押另一场 ]                                      │
└─────────────────────────────────────────────────────────────────┘
```

### 6.1.5 Magic-link 用户的导出私钥引导

如果 KPAX 在登录后探测到用户的 EOA 没有 Safe proxy 但有 Magic proxy（即用户曾用 Google/邮箱在 Polymarket 登录），显示以下引导而不是直接进入借款流程：

```
┌─────────────────────────────────────────────────────────────────┐
│ 🔑 多走一步就能用 KPAX Lending                                    │
│                                                                    │
│ 你在 Polymarket 用社交账号登录，仓位锁在 Magic 钱包里。              │
│ 借贷需要钱包对 KPAX 合约签名，请按 3 步完成迁移：                     │
│                                                                    │
│ ① 打开 Polymarket → 头像 → Settings → Export Private Key          │
│ ② 复制私钥 → 在 MetaMask 选 Import Account → 粘贴私钥                │
│ ③ 回到这里点 [我已完成]，KPAX 会重新检测                              │
│                                                                    │
│  [ 我已完成 ]   [ 详细步骤截图 ]   [ 暂不借款 ]                      │
│                                                                    │
│ ℹ️ KPAX 永远不会接触你的私钥；导出/导入全在你和 MetaMask 之间完成。    │
└─────────────────────────────────────────────────────────────────┘
```

完成后用户重连接，KPAX 探测到 EOA 现已能签名 → 走 6.1 主路径。

### 6.2 仓位监控流程

```
Lending Dashboard (个人页 → Activity → My Loans)

┌─────────────────────────────────────────────────────┐
│ Active Loans                                         │
│                                                      │
│ ┌─────────────────────────────────────────────┐    │
│ │ Man City 夺冠                  [●] 健康      │    │
│ │                                              │    │
│ │ Borrowed    $250.00                          │    │
│ │ Collateral  833 shares (~$500)               │    │
│ │ LTV         50%  ████████░░                  │    │
│ │                                              │    │
│ │ Liquidation at    LTV 80% / Kickoff - 2h     │    │
│ │ Kickoff in        6d 14h 23m                 │    │
│ │                                              │    │
│ │ Accrued interest  $0.12                      │    │
│ │ Total to repay    $250.12                    │    │
│ │                                              │    │
│ │ [ Repay ]  [ Details ]                       │    │
│ └─────────────────────────────────────────────┘    │
│                                                      │
│ Total active loans: $250                             │
│ Total interest accrued: $0.12                        │
└─────────────────────────────────────────────────────┘
```

### 6.3 LTV 预警流程

```
状态 1 (健康, LTV < 70%):     [●] 绿色 "Healthy"
状态 2 (警告, LTV 70-80%):    [●] 黄色 "Warning - add collateral or repay"
状态 3 (临近强平, LTV > 80%): [●] 红色 "Liquidation imminent" + Chrome 推送
状态 4 (Kickoff 临近, < 4h): [●] 橙色 "Auto-close in 4h" + 推送
状态 5 (强平后):              [●] 灰色 "Liquidated - no longer active"
```

Chrome 通知文案示例：
- 70%: "⚠️ Man City 仓位 LTV 达到 73%，还有 8 天到期"
- 80%: "🔴 Man City 仓位即将被强平！请立即还款或追加抵押"
- kickoff-4h: "⏰ Man City 开赛倒计时 4 小时，2 小时后将自动平仓"

### 6.4 清算流程（Keeper 触发）

> **MVP 范围（D6）**：只有 KPAX 运营的 Keeper 服务有权触发清算。Phase 2 引入 permissionless 清算 + 5% 清算者奖励。

```
KPAX Keeper (每 30s 扫描)
    ↓
检查每笔活跃借款的:
   1. 当前 token 市场价 (从 Polymarket CLOB)
   2. 累计利息
   3. 比赛开赛时间
    ↓
任一满足触发强平:
   · LTV > 80%
   · Kickoff - 2h 到达
    ↓
执行:
   1. Keeper 调用合约 liquidate(loanId)
   2. 合约内部:
      · CTF.safeTransferFrom(vault → Polymarket Exchange)
      · Exchange 卖出抵押 token 换成 USDC
      · 扣除借款本金 + 利息 + 2% 清算费
      · 剩余 USDC 从 vault 转回用户钱包
   3. loan 记录标记 liquidated
    ↓
生成用户通知 + 邮件 + 数据库记录供 AI 模型回训
```

---

## 6.5 资金托管模型（Non-Custodial）

### 核心原则
> **KPAX 团队的服务器和运营账户从来不碰用户的 CTF token 和 USDC**。
> 所有资产流转发生在链上智能合约之间，规则被审计代码锁死。

### 资产流向图

```
        开仓时                           平仓/还款时
┌──────────────────┐              ┌──────────────────┐
│ 用户 Privy 钱包   │              │ 用户 Privy 钱包   │
│ ┌─ 833 CTF ─┐    │              │ ┌─ 833 CTF ─┐    │
│ └────┬──────┘    │              │ ▲─────────  │    │
│      │ transfer  │              │ │ transfer  │    │
│      ▼           │              │ │           │    │
└──────┼───────────┘              └─┼────────────────┘
       │                            │
       ▼                            │
┌────────────────────┐              │
│ LendingVault 合约   │              │
│  (托管 CTF)         │──────────────┘
│  addr: 0xABCD...    │  合约内 logic 触发 transfer
│                     │  (按规则执行，KPAX 无法干预)
│  ┌─ LP Pool USDC ┐ │
│  └──────┬────────┘ │
│         │ transfer │
└─────────┼──────────┘
          ▼
┌──────────────────┐
│ 用户 Privy 钱包   │
│ ┌─ 250 USDC ─┐   │
│ └────────────┘   │
└──────────────────┘
```

### 四个关键约束（合约内硬编码，不可篡改）

1. **CTF token 只能在 3 个条件下离开 Vault**（D2 完全锁定）
   - 用户主动调用 `repay()` 还清债务 → 转回原用户钱包
   - Keeper 满足条件调用 `liquidate()` → 转到 Polymarket Exchange 卖出
   - 用户主动调用 `closeAndWithdraw()` → 如还清债务则放行
   - **抵押期间用户无法在 Polymarket 上交易这部分 CTF token**，直到还款释放

2. **USDC 只能在 2 个条件下流向用户**
   - 新开借款时（`openLoan`）按借款金额转出
   - 清算后剩余价值（`liquidate` 结束时）

3. **KPAX 运营钱包的权限被严格约束**
   - 可调用 `liquidate(loanId)` ← 仅当 LTV > 80% 或 kickoff - 2h
   - 可调用 `addLiquidity(amount)` / `removeLiquidity(amount)` ← 从 Treasury 注资或抽回（不影响借款）
   - **不可**：arbitrary transfer、改用户 loan 条款、跳过清算条件

4. **用户资产可链上审计**
   - Vault 合约地址公开
   - 用户可在 Polygonscan 上验证自己的 CTF token 仍然锁在合约里
   - 所有 transfer 历史可追溯

### 对比 CEX 托管模式

| 维度 | 中心化托管（FTX 式） | KPAX LendingVault（DeFi 式） |
|------|---------------------|----------------------------|
| 资产在哪里 | 平台数据库里的数字 | 链上合约的真实余额 |
| 平台能否挪用 | 能（出现问题才暴露）| 不能（合约规则约束）|
| 用户能否独立验证 | 不能 | Polygonscan 可查 |
| 破产风险对用户 | 可能完全损失 | 资产由合约锁，不受平台破产影响（但仍有合约漏洞风险）|
| 合规定位 | 证券/托管业务 | DeFi 协议（压力小）|

这个托管模型和 Aave、Compound 一致，是 DeFi 借贷的标准设计。**KPAX 充当 operator / keeper，而不是 custodian。**

### 安全保障

- **必须审计**：上线前至少通过 1 家主流审计机构（CertiK、OpenZeppelin、Trail of Bits 之一）
- **漏洞赏金**：预留 $5,000 池子，欢迎白帽汇报
- **升级机制**：合约采用 2-of-3 multisig timelock，任何升级延迟 48 小时生效

---

## 7. UI / 线框设计

### 7.1 入口设计

**入口 1 · 比赛页浮层（主力）**
在 Side Panel 比赛信息卡下方插入一个小卡片：

```
┌────────────────────────────────┐
│  💰 Borrow against this market │
│                                 │
│  You hold: 833 shares ($500)   │
│  Can borrow up to: $250        │
│                                 │
│         [ Borrow → ]           │
└────────────────────────────────┘
```

**入口 2 · 个人页 Activity 卡**（替代"Coming soon"）

**入口 3 · Header 余额浮窗**
点击 USDC 余额显示下拉：
```
Available: $125.30 USDC
Borrowed:  $250.00 (1 loan active)
[View Lending →]
```

### 7.2 Lending Dashboard 布局

```
┌────────────────────────────────────────┐
│ ← Back        Lending             ↻   │
├────────────────────────────────────────┤
│                                         │
│  Summary                                │
│  ┌────────────────────────────────┐    │
│  │ Active loans                2  │    │
│  │ Total borrowed          $450   │    │
│  │ Total collateral        $880   │    │
│  │ Weighted LTV             51%   │    │
│  └────────────────────────────────┘    │
│                                         │
│  Active Loans                           │
│  ┌────────────────────────────────┐    │
│  │ [●] Man City 夺冠              │    │
│  │     $250 / 6d 14h             │    │
│  │     LTV 50%                    │    │
│  └────────────────────────────────┘    │
│                                         │
│  ┌────────────────────────────────┐    │
│  │ [●] Arsenal vs Chelsea         │    │
│  │     $200 / 2d 3h              │    │
│  │     LTV 54%                    │    │
│  └────────────────────────────────┘    │
│                                         │
│  History (12)               [ View → ] │
└────────────────────────────────────────┘
```

### 7.3 Borrow Flow（见 6.1）

### 7.4 AI 建议浮层设计

AI 建议部分设计成可展开/折叠的卡片：

```
┌─────── 💡 AI Analysis ─────────────┐
│                                     │
│  📊 Recommended borrow: $250 (50%)  │
│  🎯 Risk score: Low (2/10)          │
│                                     │
│  [ Details ▾ ]                      │
├─────────────────────────────────────┤
│  点击展开后:                          │
│                                     │
│  基础参数                            │
│  · 联赛: 英超 (Tier 1)               │
│  · 市场深度: $2.4M (high)            │
│  · 历史年化波动: ±18%                │
│                                     │
│  关键风险因子                         │
│  · Man City 主力 Haaland 有伤疑云     │
│    → 若官方确认缺阵，token 可能跌 10%  │
│  · 距开赛 6d 14h，变数中等            │
│                                     │
│  历史相似仓位表现                     │
│  · 前 10 场同级别比赛                 │
│  · 平均 LTV 50% 仓位强平率: 4.2%     │
│  · 平均借款周期: 3.8 天              │
└─────────────────────────────────────┘
```

这个 AI 解释是 **KPAX 独有**，PolyMargin 没有这个深度。

---

## 8. 关键参数表

### 8.1 LTV 按联赛分层

| 流动性档位 | 联赛示例 | 市场深度阈值 | 最高 LTV | 警告 | 清算 |
|----------|---------|-------------|---------|------|------|
| Tier 1 (Top) | 英超、欧冠、世界杯 | > $1M | **60%** | 70% | 80% |
| Tier 2 (Mid) | 西甲、德甲、意甲、法甲、欧联、各国杯赛 | $200K–$1M | **50%** | 65% | 75% |
| Tier 3 (Low) | 英冠、荷甲、葡超等次级联赛 | $50K–$200K | **40%** | 55% | 65% |
| 不支持 | 小于 $50K 深度的市场 | — | — | — | — |

### 8.2 利率

**MVP（固定利率）**：
- 借款 APR：**12%**（行业水平：PolyMargin 未公开，Aave USDC 约 5-8%，考虑小众+无 LP 外部资金，12% 合理）
- LP APR：**8%**（KPAX Treasury 自供，但为将来外部 LP 预留分账）
- KPAX 抽成：4%（= 12% - 8%）

**Phase 2 动态利率**（类 Aave）：
```python
base = 4%
slope1 = 8%
slope2 = 200%
kink = 80%

if utilization <= 80%:
    borrow_rate = base + utilization * slope1  # 4% -> 10.4%
else:
    borrow_rate = base + kink * slope1 + (utilization - kink) * slope2  # 10.4% -> 50%+
```

### 8.3 费用

| 费用 | 金额 | 时机 |
|------|------|------|
| 开仓费 | 0 | 借款时 |
| 清算罚金 | **2%** of 抵押物价值 | 强平时 |
| 提现费 | $0.50（gas 费转嫁）| 提取 USDC 到外部钱包 |

### 8.4 借款期限与还款

- 无固定期限
- 最短：可立即还（甚至 1 秒后），无最低期限费
- 最长：到 **kickoff - 2h** 或 token 标的市场 resolution，以先到为准
- 通常借款期：3-14 天
- **支持部分还款（D4）**：任意金额都可，还款后按剩余本金重新计算 LTV
- **还款币种（D3）**：MVP 仅从用户的 Privy 钱包扣 USDC；Polymarket Balance 直接还款延后到 Phase 2
- 还清全部债务时，Vault 自动把抵押物 CTF token 转回用户钱包

### 8.5 Kickoff 缓冲

- 硬规则：距离开赛 **2 小时** 时强制平仓，规避 in-play gap risk
- 软规则：距离开赛 **4 小时** 时发送 Chrome 推送 + 邮件
- 最早可借时间：比赛开赛前至少 **24 小时**（避免刚借就强平）

---

## 9. AI 集成点（KPAX 独家）

这是我们与 PolyMargin 最大的差异化。分三层集成：

### 9.1 借款前 · 智能推荐

**输入**：用户的仓位 (tokenId, amount) + 当前市场状态
**AI 输出**：
```json
{
  "recommended_borrow_usd": 250,
  "recommended_ltv": 0.50,
  "risk_score": 2,
  "risk_reasoning": "高流动性英超比赛，历史波动率低，距开赛 6 天",
  "liquidation_probability_estimate": 0.042,
  "key_risks": [
    {"factor": "Haaland 伤停", "impact": "-10% token price"},
    {"factor": "雨战预测", "impact": "-2% token price"}
  ],
  "similar_historical_loans": {
    "count": 10,
    "avg_liquidation_rate": 0.042,
    "avg_duration_days": 3.8
  }
}
```

**实现**：调用后端 `/api/lending/risk-assessment`，复用现有 `quick_preview` 的 LLM 调用逻辑，替换 prompt。

### 9.2 借款中 · 实时风险预警

**触发条件**：
- 抵押 token 价格 24h 内跌 > 5%
- 关键事件（伤停、阵容变化）被新闻源识别
- 距开赛 < 6h 进入倒计时

**输出**：Chrome 通知 + Side Panel Banner
```
⚠️ Man City 仓位更新
Haaland 已官宣缺阵，token 跌 8%，LTV 从 50% 升至 58%
建议：还款 $50 or 追加 1 场底仓
[ Take action ]
```

### 9.3 借款后 · 后验分析

结束时 AI 生成一段总结：
```
本次借款回顾
· 抵押: Man City 夺冠
· 借款: $250 @ 50% LTV，持续 5.2 天
· 结果: 还款 $250.43（含利息 $0.43）
· 抵押物最终价值: $540（+8%），回报率 = +2.0% APR
· AI 预测 vs 实际: 预测强平率 4.2%，实际未触发 ✓
```

这个后验信息用来：
- 提升用户对 KPAX 模型的信任
- 改进 AI 模型（回训）
- 积累用户 profile（未来做个性化推荐）

---

## 10. 风险管理

### 10.1 用户侧风险

| 风险 | 用户视角 | 缓解措施 |
|------|---------|---------|
| 爆仓 | 损失抵押 token | LTV 分档 + 实时预警 + 软清算 |
| 借款后忘记还 | 累积利息 + 被强平 | 邮件/Chrome 推送 + 距开赛倒计时 |
| 价格操纵被拉爆 | 遭遇 token 被砸盘 | 5min TWAP 价格 + 异常熔断 |

### 10.2 平台侧风险

| 风险 | 平台后果 | 缓解措施 |
|------|---------|---------|
| 坏账（清算滑点） | LP 损失 | 限制单市场敞口 + 1% 保险基金 + 白名单流动性过滤 |
| **合约漏洞**（重入 / 权限错配 / overflow）| 用户资产被盗 / 冻结 | **上线前审计**（CertiK / OpenZeppelin）+ 漏洞赏金 + 升级 timelock |
| 预言机故障 | 错误强平 / 错失强平 | Polymarket + 自研双源喂价 + 熔断阈值 |
| Keeper 离线 | 无法及时强平 | Keeper 冗余 + 后端多节点 + permissionless 清算奖励 |
| 级联清算（单 token 多借款同时爆仓）| 流动性死亡螺旋 | 单 token 总借款 ≤ 20% 市场深度 + 软清算分批卖出 |
| KPAX 自身被攻击 / 运营密钥泄露 | Keeper 误调用 / Treasury 被挪用 | 2-of-3 multisig + hardware key + 合约约束（见 6.5 节）|

详见 `docs/leverage-products-comparison.md` 第六节的级联清算分析。

### 10.3 合规风险

| 风险 | 缓解 |
|------|------|
| 美国 SEC 认定为衍生品 | 明确 Terms of Service 说"借贷工具而非投资产品"；Privy 非托管钱包弱化"平台控制" |
| 用户未通过 KYC | MVP 限制单用户最大借款 $1,000（最大坏账可控） |
| 数据合规 | Privy 处理 PII，KPAX 只存 privy_user_id + wallet_address |

---

## 11. 商业模式与收入

### 11.1 收入来源

| 来源 | MVP（月）| Phase 2（月） |
|------|---------|--------------|
| 利率差 | 4% × TVL | 8% × TVL |
| 清算罚金分成 | 2% × 清算量 × 50% | 同 |
| 提现 gas 分成 | $0.50 × 次数 | 同 |

假设 Phase 2 TVL $100K，年化收入估算：
```
利率差收入:     $100K × 8% = $8,000/年
清算罚金收入:    $100K × 5%(年清算率) × 2% × 50% = $50/年
提现费收入:      100 次/月 × $0.50 = $600/年
──────────────────────────────
总计:           $8,650/年
```

这个规模对早期公司很小，但：
1. **Lending 是用户留存和粘性的关键**，TVL 越高用户越离不开
2. **为保险产品提供 LP 基础**（LP 池可跨产品）
3. **为 Token 发行（如果做）埋下 TVL 基础**

### 11.2 商业目标

**MVP（Phase 1）：活下来 + 验证**
- DAU/MAU 增长
- TVL > $50K
- 坏账率 < 2%

**Phase 2：开放 LP + 扩大规模**
- TVL > $500K
- 年化收入 > $50K
- 外部 LP 占比 > 30%

---

## 12. 成功指标 (KPI)

### 12.1 北极星指标
**已借款用户占插件周活 DAU 的比例（Borrow Adoption Rate）**
- Week 4 目标：1%
- Week 12 目标：5%
- Week 24 目标：10%

### 12.2 二级指标

| 类别 | 指标 | Week 4 | Week 12 |
|------|------|--------|---------|
| 规模 | TVL (active loans) | $10K | $100K |
| 规模 | 累计借款次数 | 50 | 500 |
| 健康 | 坏账率（损失/总借款） | < 3% | < 1% |
| 健康 | 清算率（清算数/总数） | < 10% | < 5% |
| 体验 | 平均借款完成时间（点 Borrow → USDC 到账） | < 60s | < 30s |
| 体验 | AI 建议采纳率（用户是否接受推荐 LTV） | 30% | 60% |
| 留存 | 重复借款率 | 20% | 50% |

### 12.3 需要埋点的事件

```
lending.dashboard.view
lending.position.view
lending.borrow.intent_click
lending.borrow.ai_suggest_shown
lending.borrow.ai_suggest_accepted | overridden
lending.borrow.amount_adjusted
lending.borrow.confirmed
lending.borrow.success
lending.borrow.error
lending.repay.initiated
lending.repay.success
lending.liquidation.triggered_ltv
lending.liquidation.triggered_kickoff
lending.alert.sent_70 | sent_80 | sent_kickoff
```

---

## 13. 实施路线图

### Phase 1 · MVP (Week 1-6)

**Week 1-2**：智能合约 + 后端
- [ ] `LendingVault.sol` 开发 + 单元测试
- [ ] 后端 DB schema（loans 表）+ Alembic migration
- [ ] `/api/lending/*` endpoints（list, borrow, repay）
- [ ] Privy 交易签名集成

**Week 3-4**：前端 + AI 集成
- [ ] Side Panel 比赛页"Borrow"入口
- [ ] Lending Dashboard
- [ ] Borrow Flow（AI 建议 + 金额选择 + 签名）
- [ ] `/api/lending/risk-assessment`（AI prompt）
- [ ] 个人页 Activity 卡接真数据

**Week 5-6**：风控 + 测试
- [ ] Keeper 服务（定时扫描 + 清算）
- [ ] Chrome 通知 + 邮件预警
- [ ] 压测：单 token 级联清算模拟
- [ ] Alpha 测试（内部 3-5 个用户）

### Phase 1.5 · 公测 (Week 7-9)

- [ ] 邀请 20 名早期用户封闭测试
- [ ] 坏账率监控
- [ ] Bug 修复 + 参数调优
- [ ] 改进 AI prompt

### Phase 2 · 扩展 (Week 10-24)

- 开放外部 LP + kpaxUSD 凭证
- 动态利率曲线
- 多 token 组合抵押
- 非体育市场（选 Tier 1：热门加密、政治）
- 第一次审计

### Phase 3 · 与其他产品联动 (Week 25+)

- Insurance + Lending 套餐
- Leverage 产品（Phase 3 核心）
- $KPAX 代币（如果监管路径可行）

---

## 14. 冷启动策略

### 14.1 资金侧（LP）
**MVP 阶段**：KPAX Treasury 注入 $50K USDC 作为初始 LP 池。
- 不接受外部 LP（降低运营复杂度、聚焦产品打磨）
- Treasury 回报用作运营成本 / 团队 bonus
- 当 utilization 持续 > 60% 持续 3 周，考虑补充资金或开放 LP

### 14.2 用户侧（Borrower）
**引流钩子**（按优先级）：

1. **现有 KPAX Pro 用户定向推荐**（Chrome 通知）
2. **Side Panel 比赛页入口**（所有用户直接看到）
3. **"首笔借款免息 7 天"**（新用户激活）
4. **KPAX 官方 X / Discord 宣发**
5. **Polymarket 论坛 / reddit 软文**（技术向，讲 AI 风控）

### 14.3 前期激励

| 激励 | 成本 | 预期带量 |
|------|------|---------|
| 首笔借款免息 7 天（上限 $500）| 50 用户 × $500 × 7/365 × 12% = $57 | 50 人首借 |
| 老用户推荐奖金 $5 | 每成功推荐 1 人 | 50 人推荐 |
| AI 建议采纳反馈 | 纯 UX，零成本 | 数据积累 |

---

## 15. 已决策问题（v0.1 定稿）

以下 8 个问题在 2026-04-22 已按"建议"拍板，进入实现阶段。

| # | 问题 | 决策 |
|---|------|------|
| D1 | KPAX Treasury 自运营 LP 的合规性 | **MVP 照做**：内部资金、无对外分红，不构成证券业务。Phase 2 开放外部 LP 前必须做合规审查。 |
| D2 | 抵押期间能否同时交易该 token | **MVP 完全锁定**：抵押物进 Vault 后不可再动。用户可以再抵押另一份额开新借款，但已抵押份额不可撤回（除还款）。 |
| D3 | 还款的 USDC 来源 | **MVP 仅支持 Privy 钱包余额**。从 Polymarket Balance 还款延后到 Phase 2。 |
| D4 | 部分还款 | **允许任意金额部分还款**。还款后按剩余本金重新计算 LTV；一旦还清自动释放抵押。 |
| D5 | 用户推翻 AI LTV 建议 | **警告后允许**，硬上限为该联赛 Tier 的 LTV 上限（Tier 1 60% / Tier 2 50% / Tier 3 40%）。不收风险溢价（MVP 简化）。 |
| D6 | 清算触发方 | **MVP 仅 KPAX Keeper** 独立触发。Phase 2 引入 permissionless 清算 + 5% 清算者奖励。 |
| D7 | 集成 PolyMargin 对比展示 | **暂不做**。集中精力优化自身体验，不做比较营销。 |
| D8 | 强制 Terms of Service | **必须**。用户首次借款前弹出 ToS 同意框，签过一次后永久有效；ToS 版本变更时重新弹出。 |

这些决策直接影响以下章节的实现细节，对应规则已在 §5（Scope）/ §6（Flow）/ §7（UI）中逐一落地。

---

## 16. 里程碑与决策点

**决策点 A（Week 0 结束）**：
- LTV 分层参数确认
- 利率模型确认
- 合约架构 review 通过

**决策点 B（Week 6 内测前）**：
- Keeper 稳定性达标（模拟 100 场清算 0 失败）
- 坏账模型验证（模拟 TVL $100K，坏账 < 2%）

**决策点 C（Week 9 公测后）**：
- DAU 是否达到北极星 1% 阈值
- 用户反馈 NPS > 40
- 决定是否按计划进 Phase 2 或延迟

---

## 附录

### A. 术语
- **LTV**：Loan-to-Value，借款金额/抵押物价值
- **TVL**：Total Value Locked，活跃借款总额
- **Utilization**：借出资金 / LP 池总额
- **Kickoff**：比赛开球时间
- **Liquidation Penalty**：强平时扣的额外费用（补偿 LP 承担的风险）

### B. 参考
- PolyMargin 研报：`docs/polymargin-research.md`
- 竞品对比：`docs/competitive-analysis.md`、`docs/leverage-products-comparison.md`
- 技术方案：`docs/tech-plan-centralized.md`

### C. 版本历史
- v0.1 (2026-04-22)：初版，基于 PolyMargin 研报 + KPAX 现有产品形态
- v0.2 (2026-04-25)：弄清 Polymarket 账户结构（EOA + proxy）后大改：
  - §3.1 加 Casey 第三画像（Magic-link 用户），§3.3 反画像调整
  - §5.1 新增"登录 + 钱包检测""Magic 引导""proxy 一次性授权"三个模块
  - §6 新增 §6.0 钱包架构前提、§6.1.5 Magic 用户导出私钥引导
  - §6.1 借款 step1 改为 Safe `execTransaction(setApprovalForAll)`，借款执行从 EOA 改为 proxy 作为 collateralSource
  - 链上派生公式（Safe + Magic 两套）已用真实数据验证通过
