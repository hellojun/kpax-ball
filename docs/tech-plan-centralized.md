# KPAX Ball 保险 + 杠杆 技术方案（中心化）

> 核心思路：用户用 Privy 登录并持有钱包（KPAX 不托管私钥），KPAX 后端只做业务逻辑、对赌账本和自动化服务。
> 适合 MVP 快速验证，同时规避资金托管的合规风险。

---

## 一、系统架构

```
┌──────────────┐     ┌──────────────────────────┐     ┌──────────────┐
│  Chrome 插件  │     │      KPAX Backend         │     │   Polygon    │
│              │     │       (FastAPI)            │     │              │
├──────────────┤     ├──────────────────────────┤     ├──────────────┤
│ Privy 登录    │     │ 用户会话（基于 Privy JWT）  │     │              │
│  └─ 嵌入钱包  │     │                            │     │              │
│              │     │ 业务账本 (DB)              │     │              │
│ 查询保险报价  │───► │  · 保单                    │     │              │
│ 查询仓位      │───► │  · 杠杆仓位                │     │              │
│              │     │                            │     │              │
│ 签名交易      │───► │ 构造交易参数                │     │              │
│ (Privy 签名)  │     │  · 保费转账                 │───►│ InsurancePool│
│              │     │  · 杠杆授权                 │───►│ LeverageProxy│
│              │     │                            │     │              │
│              │     │ ┌──────────────────────┐ │     │              │
│              │     │ │ 后台服务              │ │     │              │
│              │     │ │ · 链上事件监听        │◄├─────│ 合约事件      │
│              │     │ │ · 赛后结算 (cron)     │─├────►│ 触发合约      │
│              │     │ │ · 强平 keeper         │─├────►│ liquidate()  │
│              │     │ │ · 价格监控 (ws)       │◄├─────│ CLOB 价格     │
│              │     │ └──────────────────────┘ │     │              │
│              │     ├──────────────────────────┤     │              │
│              │     │ 数据库                    │     │              │
│              │     │ · users (privy_user_id)   │     │              │
│              │     │ · insurance_policies      │     │              │
│              │     │ · leverage_positions      │     │              │
│              │     │ · settlements             │     │              │
└──────────────┘     └──────────────────────────┘     └──────────────┘
```

**中心化 vs 去中心化的差异**：
- 资金托管：**非托管**（用户钱包在 Privy，KPAX 无法挪用）
- 业务账本：**中心化**（保单、仓位、盈亏都在 KPAX 后端数据库）
- 资金通道：**链上**（USDC 在用户钱包和合约之间流转）
- 决策逻辑：**中心化**（定价、强平、结算都由 KPAX 后端触发）

---

## 二、用户登录与钱包（Privy）

### 2.1 Privy 集成

Privy 提供嵌入式钱包服务。用户可以用 Email / Google / Apple / Twitter 登录，Privy 通过 MPC 为用户创建 Polygon 钱包。**KPAX 只能拿到钱包地址，拿不到私钥。**

```
用户打开 KPAX 插件
  ↓
点击"连接钱包" → 弹出 Privy 登录 UI
  ↓
选择登录方式（Google / Email / ...）
  ↓
Privy 创建（或恢复）用户的 MPC 钱包
  → 私钥分片：一部分在 Privy 服务器，一部分在用户设备
  → 用户随时可导出私钥，Privy 无法单独动用
  ↓
KPAX 拿到：
  · Privy user ID（作为 KPAX 的唯一用户标识）
  · Polygon 钱包地址
  · Privy JWT（用于调 KPAX 后端 API）
```

### 2.2 后端用户表

```python
# models/user.py

class User(Base):
    __tablename__ = "users"

    id: int                     # PK
    privy_user_id: str          # Privy 用户 ID（唯一）
    wallet_address: str         # Polygon 钱包地址
    email: str | None           # 可选
    created_at: datetime
    last_login_at: datetime

# 注意：
# - 不存私钥（Privy 托管，KPAX 无权访问）
# - 不存 USDC 余额（用户资金在链上，用合约/钱包余额查询）
```

### 2.3 API 认证

前端每次调 KPAX 后端时，带上 Privy 签发的 JWT：

```
Authorization: Bearer <privy_access_token>
  ↓
后端用 Privy 公钥验证 JWT 签名
  ↓
从 JWT 提取 privy_user_id → 查询本地 User
  ↓
正常响应
```

### 2.4 充值（不需要 KPAX 介入）

用户的 USDC 充值完全绕过 KPAX：

```
用户从其他钱包 / 交易所 / Polymarket 转 USDC 到自己的 Privy 钱包
  ↓
KPAX 只需在需要时通过 RPC 查询 USDC 余额：
  balance = usdc.balanceOf(user_wallet_address)
  ↓
或者通过 Privy SDK 直接显示余额
```

---

## 三、保险系统（对赌模式）

### 3.1 核心逻辑

KPAX 本质上是做市商。虽然业务账本在后端，但**资金流走链上**：
- 保费从用户钱包 → KPAX 的 InsurancePool 合约
- 赔付从 InsurancePool 合约 → 用户钱包

```
用户买了 "Man City 主胜"，想买保险
  ↓
KPAX 后端计算保费：
  保费 = f(市场赔率, KPAX AI 概率, 保障比例, 赛前时间) = $22
  赔付金额 = $70
  ↓
前端请求用户签名：
  "批准 InsurancePool 合约从你钱包扣 $22 USDC"
  → Privy 弹窗确认 → 用户签名
  ↓
后端构造并发送交易：
  InsurancePool.purchaseInsurance(
    matchId, direction, premium=22, payout=70
  )
  ↓
链上执行：
  1. 从用户钱包转 $22 USDC 到 InsurancePool
  2. 合约 emit PolicyCreated 事件
  ↓
KPAX 后端监听到 PolicyCreated 事件：
  → 在数据库创建 insurance_policies 记录（status=active）
  → 前端显示"保险已生效"
  ↓
赛后自动结算：
  查询比赛结果
  ├── Man City 赢 → 保险作废（合约里的 $22 留在池子里）
  └── Man City 输 → 后端调用 InsurancePool.payout(policyId)
                    → 合约从池子转 $70 USDC 到用户钱包
```

### 3.2 InsurancePool 合约（简化版）

不是完整的去中心化合约，只是一个"带权限的资金池"，KPAX 后端作为唯一的 operator。

```solidity
// InsurancePool.sol（中心化版本，KPAX 控制权限）

contract InsurancePool {
    IERC20 public usdc;
    address public operator;  // KPAX 后端地址

    struct Policy {
        address user;
        uint256 premium;
        uint256 payout;
        bytes32 matchId;
        uint8 direction;
        bool settled;
    }

    mapping(uint256 => Policy) public policies;
    uint256 public nextPolicyId;
    uint256 public poolBalance;  // 用于赔付的资金

    // 用户购买保险（由后端代提交，用户签名 Permit）
    function purchaseInsurance(
        address user,
        uint256 premium,
        uint256 payout,
        bytes32 matchId,
        uint8 direction,
        bytes calldata permitSig  // 用户的 EIP-2612 Permit 签名
    ) external onlyOperator returns (uint256 policyId) {
        // 用 Permit 授权 + 转账，一步到位，用户只需签名一次
        IERC20Permit(address(usdc)).permit(user, address(this), premium, ...);
        usdc.transferFrom(user, address(this), premium);

        policyId = nextPolicyId++;
        policies[policyId] = Policy(user, premium, payout, matchId, direction, false);
        emit PolicyCreated(policyId, user, premium, payout);
    }

    // KPAX 后端赛后结算（输了才调）
    function payout(uint256 policyId) external onlyOperator {
        Policy storage p = policies[policyId];
        require(!p.settled, "Already settled");
        p.settled = true;
        usdc.transfer(p.user, p.payout);
        emit Payout(policyId, p.user, p.payout);
    }

    // KPAX 注资（用自己的钱做市）
    function fundPool(uint256 amount) external {
        usdc.transferFrom(msg.sender, address(this), amount);
        poolBalance += amount;
    }

    // KPAX 提取利润
    function withdrawProfit(uint256 amount) external onlyOperator {
        // 保留一定比例用于赔付，不能全部提走
        ...
    }
}
```

### 3.3 定价引擎（纯后端）

```python
# services/insurance_pricer.py

def calculate_premium(
    insured_amount: float,      # 保障金额，如 $100
    coverage_ratio: float,      # 保障比例，如 0.7
    market_prob: float,         # 市场赔率（用户投注方向的概率），如 0.74
    kpax_prob: float | None,    # KPAX AI 概率（可选），如 0.68
    hours_to_match: float,      # 距比赛开始的小时数
) -> dict:
    payout = insured_amount * coverage_ratio  # $70
    loss_prob = 1 - market_prob               # 0.26

    # AI 调整系数
    ai_factor = (1 - kpax_prob) / (1 - market_prob) if kpax_prob else 1.0

    # 时间衰减：越接近比赛，保费越贵
    time_factor = max(1.0, 1.3 - hours_to_match / 72)

    # 保费 = 预期赔付 × AI 调整 × 时间调整 × 利润率
    premium = payout * loss_prob * ai_factor * time_factor * 1.3

    return {
        "premium": round(premium, 2),
        "payout": round(payout, 2),
        "coverage_ratio": coverage_ratio,
    }
```

### 3.4 数据模型

```python
# models/insurance.py

class InsurancePolicy(Base):
    __tablename__ = "insurance_policies"

    id: int
    user_id: int                    # FK → users.id
    wallet_address: str             # 用户钱包地址（冗余，方便查询）
    match_slug: str
    home_team: str
    away_team: str
    competition: str

    insured_direction: str          # home | away | draw
    insured_amount: float
    coverage_ratio: float
    premium: float
    payout: float

    market_prob_at_purchase: float
    kpax_prob_at_purchase: float

    onchain_policy_id: int          # InsurancePool 合约里的 policyId
    purchase_tx_hash: str           # 购买时的链上交易哈希
    settlement_tx_hash: str | None  # 赔付交易哈希

    status: str                     # active | settled_win | settled_payout | expired
    settlement_result: str
    settled_at: datetime | None

    created_at: datetime
    match_time: datetime
```

### 3.5 API 接口

```
POST /api/insurance/quote              # 获取报价（不扣费）
POST /api/insurance/prepare-purchase   # 构造 Permit 签名消息（返回 typed data）
POST /api/insurance/confirm-purchase   # 提交用户签名 + 调合约
GET  /api/insurance/policies           # 我的保单列表
GET  /api/insurance/policy/{id}        # 保单详情
```

### 3.6 前端交互：下单时提醒

```
Content Script 监听 Polymarket 页面：

方式 1: MutationObserver 检测交易确认弹窗出现
方式 2: 拦截 fetch/XHR 请求，监听 Polymarket 下单 API
方式 3: 检测页面"Order Confirmed"文案

检测到用户下单后：
  ↓
在页面右下角弹出浮层（不是 Side Panel）：
┌─────────────────────────────────────┐
│ 🛡️ KPAX Insurance                   │
│                                     │
│ Protect your Man City bet?          │
│ Pay $22 → Get $70 back if you lose  │
│                                     │
│ [Buy Insurance]     [No thanks]     │
└─────────────────────────────────────┘
  ↓
点击 Buy Insurance → 打开 Side Panel → Privy 签名 → 上链
```

### 3.7 赛后结算

```python
# services/settlement.py（每小时运行一次）

async def settle_insurance_policies():
    policies = db.query(InsurancePolicy).filter(
        InsurancePolicy.status == "active",
        InsurancePolicy.match_time < datetime.utcnow() - timedelta(hours=3),
    ).all()

    for policy in policies:
        result = await get_match_result(policy.match_slug)
        if not result:
            continue

        policy.settlement_result = result

        if result == policy.insured_direction:
            # 用户在 PM 赢了，保险不触发
            policy.status = "settled_win"
        else:
            # 用户在 PM 输了，调合约赔付
            tx_hash = await insurance_contract.payout(policy.onchain_policy_id)
            policy.status = "settled_payout"
            policy.settlement_tx_hash = tx_hash

        policy.settled_at = datetime.utcnow()

    db.commit()
```

### 3.8 KPAX 风控

```
1. 单场比赛最大敞口：同一场比赛所有保单总赔付 < $10,000
2. 单用户限制：单笔 < $500，累计未结算 < $2,000
3. 赔率极端值拒绝：市场赔率 > 90% 或 < 10% 不提供保险
4. 资金池监控：InsurancePool 余额 < 总敞口的 50% 时暂停新保单
5. 对冲选项：单方向保单过多时，KPAX 可在 Polymarket 买反向头寸对冲
```

---

## 四、杠杆系统

### 4.1 核心逻辑

用户想开杠杆，但私钥在 Privy（用户控制）。KPAX 怎么强制平仓？

**方案：使用 ERC-4337 Session Key / Account Abstraction 授权**

```
用户首次开杠杆时：
  签一次授权 → KPAX 可以在满足条件时代为执行特定操作
  授权内容限定：
    · 只能调用 LeverageVault 合约
    · 只能执行 liquidate(positionId) / forceCloseAtKickoff(positionId) 函数
    · 只能在持仓价值 < 强平线时执行
    · 不能转走用户其他资产
    · 授权有效期：杠杆仓位期间
    · 用户可随时撤销
```

### 4.1.1 关键约束：**仅支持赛前杠杆（Pre-Match Only）**

体育比赛与加密货币的本质区别：价格是**事件驱动、跳空剧烈**。进球 / 红牌发生时，Polymarket 的赔率可能在几百毫秒内从 0.60 直接打到 0.30，任何强平 keeper（无论多快）都来不及在跳空前执行交易——穿透后再强平会造成坏账。

MVP 阶段采取最简方案：**杠杆只支持赛前，开球（kickoff）前 10 分钟强制平仓**。

```
理由：
1. 体育信息差最大的环节在赛前（伤病、阵容、天气、教练策略）
2. 用户用杠杆多数是放大赛前判断，而非 in-play 追逐
3. 完全消除比赛中的跳空风险，工程量降 ~50%
4. 风控模型简单：只需要处理赛前的连续价格波动

用户体验：
- 开仓：仅当 match_time > now + 10min 时可开
- 监控：Keeper 每 5 秒通过 CLOB WebSocket 拉取价格
- 强平：触及维持保证金线（仓位价值 < 债务 × 1.2）时立即强平
- Kickoff 前 10min：无论盈亏，全部强制平仓（按当前市价结算）
- 比赛中：用户若想继续持有，可在 PM 上重新下单（无杠杆）
```

### 4.1.2 Phase 2：开放 In-Play 杠杆（不在本期范围）

当需要在比赛中支持杠杆时，需要叠加以下保护层：
1. **分层维持保证金**：赛前 20% / 比赛中 50%
2. **分级杠杆倍数**：赛前最高 5x / 比赛中最高 2x
3. **预警式渐进减仓**：到 1.3× 维持线就开始部分减仓
4. **保险基金（Insurance Fund）**：从利息 + 强平费抽成，坏账兜底
5. **大事件冷却**：进球 / 红牌后 30s 禁止开新仓

Phase 2 不在 MVP 实施范围，本方案不展开。

### 4.2 开仓流程

```
开仓前置检查：
  · match_time > now + 10min（距开球至少 10 分钟）
  · 市场有足够流动性
  · 用户未超出单用户敞口上限

用户选择：3 倍杠杆买 "Man City 主胜"，保证金 $100
  ↓
前端向后端请求开仓参数
  ↓
后端返回：
  · collateral = $100
  · borrowed = $200（KPAX 借出）
  · total = $300
  · 交易数据（两笔）：
    1. USDC.approve(LeverageVault, $100)
    2. LeverageVault.openPosition(matchId, direction, $100, 3x, sessionKey)
  ↓
Privy 请求用户签名（两笔合并为一次 UserOperation）
  ↓
链上执行：
  1. LeverageVault 从用户钱包扣 $100 保证金
  2. LeverageVault 从 KPAX 借贷池借 $200
  3. LeverageVault 用总共 $300 在 Polymarket 买入（通过 Polymarket Exchange）
  4. Token 锁在 LeverageVault 合约里
  5. 记录 session key 授权给 KPAX keeper
  ↓
后端监听到 PositionOpened 事件 → 数据库创建 position 记录
```

### 4.3 LeverageVault 合约

```solidity
// LeverageVault.sol（简化版，KPAX 控制 keeper）

contract LeverageVault {
    IERC20 public usdc;
    IConditionalTokens public ctf;       // Polymarket CTF
    IPolymarketExchange public exchange;
    address public keeper;                // KPAX keeper 地址

    struct Position {
        address user;
        bytes32 matchId;
        uint256 tokenId;
        uint8 direction;
        uint256 collateral;
        uint256 borrowed;
        uint256 shares;
        uint256 entryPrice;
        uint256 openTime;
        bool active;
    }

    mapping(uint256 => Position) public positions;
    uint256 public lendingPool;  // KPAX 注入的借贷资金

    // 用户开仓
    function openPosition(
        address user,
        bytes32 matchId,
        uint256 tokenId,
        uint8 direction,
        uint256 collateral,
        uint256 leverageBps
    ) external returns (uint256 positionId) {
        uint256 borrowed = collateral * (leverageBps - 10000) / 10000;
        uint256 total = collateral + borrowed;

        require(lendingPool >= borrowed, "Insufficient lending pool");
        usdc.transferFrom(user, address(this), collateral);
        lendingPool -= borrowed;

        // 在 Polymarket 买入
        usdc.approve(address(exchange), total);
        uint256 shares = exchange.buy(tokenId, total);

        positionId = nextPositionId++;
        positions[positionId] = Position(user, matchId, tokenId, direction,
            collateral, borrowed, shares, getCurrentPrice(tokenId),
            block.timestamp, true);

        emit PositionOpened(positionId, user, collateral, borrowed);
    }

    // Keeper 强平（受授权限制，仅赛前触及维持保证金线）
    function liquidate(uint256 positionId) external onlyKeeper {
        Position storage pos = positions[positionId];
        require(pos.active, "Not active");
        require(isLiquidatable(positionId), "Not liquidatable");

        // 在 Polymarket 卖出
        uint256 proceeds = exchange.sell(pos.tokenId, pos.shares);

        // 分配资金
        uint256 interest = calculateInterest(pos);
        uint256 debt = pos.borrowed + interest;
        uint256 liquidationFee = proceeds * 200 / 10000;  // 2%

        uint256 kpaxReceives = min(proceeds, debt + liquidationFee);
        uint256 userReceives = proceeds > kpaxReceives ? proceeds - kpaxReceives : 0;

        lendingPool += pos.borrowed;
        if (userReceives > 0) usdc.transfer(pos.user, userReceives);

        pos.active = false;
        emit PositionLiquidated(positionId, proceeds, userReceives);
    }

    // Kickoff 前 10 分钟强制平仓（无论盈亏）
    // 由 KPAX keeper 统一触发，规避比赛中跳空风险
    function forceCloseAtKickoff(uint256 positionId) external onlyKeeper {
        Position storage pos = positions[positionId];
        require(pos.active, "Not active");
        require(block.timestamp >= matchKickoff[pos.matchId] - 10 minutes, "Too early");

        uint256 proceeds = exchange.sell(pos.tokenId, pos.shares);

        uint256 interest = calculateInterest(pos);
        uint256 debt = pos.borrowed + interest;

        // 不收强平费（用户不是被强平，是系统规则）
        uint256 kpaxReceives = min(proceeds, debt);
        uint256 userReceives = proceeds > kpaxReceives ? proceeds - kpaxReceives : 0;

        lendingPool += pos.borrowed;
        if (userReceives > 0) usdc.transfer(pos.user, userReceives);

        pos.active = false;
        emit PositionForceClosed(positionId, proceeds, userReceives);
    }

    // 用户主动平仓（仅赛前可用）
    function closePosition(uint256 positionId) external {
        Position storage pos = positions[positionId];
        require(msg.sender == pos.user, "Not owner");
        require(block.timestamp < matchKickoff[pos.matchId] - 10 minutes, "Kickoff window");
        // ... 类似 liquidate 但不收强平费
    }
}
```

### 4.4 Keeper 服务（后端）

由于**仅支持赛前杠杆**，价格变化是连续的（不存在 in-play 跳空），但仍需足够的监控频率。采用 **CLOB WebSocket + 5 秒轮询** 的双层机制。

```python
# services/leverage_keeper.py

# ------ Task 1: 实时价格监听（WebSocket 常驻）------
async def price_watcher():
    """
    连接 Polymarket CLOB WebSocket，订阅所有活跃仓位的 tokenId。
    收到价格变化时，立即评估是否触发强平。
    """
    async with polymarket_clob_ws() as ws:
        active_tokens = await get_active_position_tokens()
        await ws.subscribe(active_tokens)

        async for msg in ws:
            token_id = msg["tokenId"]
            price = msg["midPrice"]
            positions = get_positions_by_token(token_id)

            for pos in positions:
                position_value = pos.shares * price
                debt = pos.borrowed + calculate_interest(pos)

                if position_value <= debt * 1.2:  # 维持保证金 20%
                    await trigger_liquidation(pos)

# ------ Task 2: Kickoff 前 10 分钟强制平仓 ------
async def kickoff_force_close():
    """
    每 30 秒扫描一次，找到距开球 <= 10 分钟的仓位，全部强制平仓。
    """
    window_start = datetime.utcnow() + timedelta(minutes=10)
    positions = db.query(LeveragePosition).filter(
        LeveragePosition.status == "active",
        LeveragePosition.match_time <= window_start,
    ).all()

    for pos in positions:
        tx = leverage_vault.functions.forceCloseAtKickoff(pos.onchain_id).transact()
        await wait_for_confirmation(tx)
        pos.status = "force_closed_kickoff"
        pos.closed_at = datetime.utcnow()
        await notify_user(pos.user_id,
            "Your leverage position was auto-closed before kickoff.")

    db.commit()

# ------ Task 3: 兜底轮询（防 WebSocket 断连）------
async def fallback_polling():
    """
    每 5 秒拉一次 REST 价格作为兜底。
    如果 WebSocket 因任何原因丢失，这里仍能在 5 秒内发现强平条件。
    """
    while True:
        await check_all_positions_via_rest()
        await asyncio.sleep(5)
```

**监控层级说明：**

| 机制 | 频率 | 作用 |
|------|------|------|
| CLOB WebSocket | 实时（~100ms） | 主要强平触发 |
| REST 兜底轮询 | 5 秒 | WebSocket 断连保护 |
| Kickoff 扫描 | 30 秒 | 开球前强制平仓 |
| 赛后清理 | 1 小时 | 清理异常状态仓位 |

### 4.5 数据模型

```python
# models/leverage.py

class LeveragePosition(Base):
    __tablename__ = "leverage_positions"

    id: int
    user_id: int
    wallet_address: str
    match_slug: str
    home_team: str
    away_team: str

    direction: str
    leverage_ratio: float
    collateral: float
    borrowed: float
    total_position: float

    entry_price: float
    shares: float
    token_id: str

    liquidation_price: float
    interest_rate: float

    onchain_position_id: int    # LeverageVault 合约里的 positionId
    open_tx_hash: str
    close_tx_hash: str | None

    status: str                 # active | liquidated | force_closed_kickoff | closed_by_user
    pnl: float
    closed_at: datetime | None

    created_at: datetime
    match_time: datetime         # 开球时间，用于 kickoff 强平判断
```

### 4.6 API 接口

```
POST /api/leverage/prepare-open        # 构造开仓交易参数
POST /api/leverage/confirm-open        # 用户签名后提交
POST /api/leverage/close/{id}          # 用户主动平仓
GET  /api/leverage/positions           # 我的仓位（含实时盈亏）
GET  /api/leverage/config              # 杠杆配置（倍数、费率）
```

---

## 五、前端新增页面

```
Side Panel 底部导航：

[分析]  [保险]  [杠杆]

分析 Tab：现有功能不变

保险 Tab：
  ├── 当前比赛的保险报价
  ├── 我的保单列表（onchain_policy_id 可以点击查看链上交易）
  └── 结算历史

杠杆 Tab（仅赛前）：
  ├── 开仓面板
  │    · 距开球 < 10min 时禁用
  │    · 显眼提示："杠杆仅支持赛前，开球前 10 分钟自动平仓"
  ├── 当前仓位
  │    · 实时盈亏 / 强平线 / 链上仓位链接
  │    · 距开球倒计时（< 30min 变红提醒）
  └── 历史仓位（含 force_closed_kickoff 状态）

余额显示：顶部常驻
  · USDC 余额（从用户钱包查询）
  · 充值提示（引导用户转 USDC 到自己的钱包地址）
  · 不需要"提现"——用户的钱从来没离开过自己的钱包
```

---

## 六、Polymarket 下单集成

杠杆需要合约代替用户在 Polymarket 下单。

**关键：LeverageVault 合约作为 Polymarket CLOB 的用户**

```
Polymarket CLOB 支持"proxy trading"：
  · 部署 LeverageVault 时，让它作为一个 Polymarket 账户
  · KPAX 为 Vault 注册 Polymarket API Key
  · Vault 的交易通过 Exchange 合约执行（链上）

流程：
  1. 用户 → LeverageVault.openPosition($300)
  2. Vault 内部调用：exchange.fillOrder(buyOrder, $300)
  3. Exchange 合约从 Vault 扣 USDC，mint Conditional Token 给 Vault
  4. Token 锁在 Vault 里（用户无法直接取走）
```

---

## 七、实施计划

| 阶段 | 内容 | 时间 |
|------|------|------|
| **P1: Privy 集成** | 替换 Google 登录，前端 + 后端对接 Privy | 1 周 |
| **P2: 保险合约** | InsurancePool 合约开发 + 部署 + 前后端集成 | 2 周 |
| **P3: 下单提醒** | Content Script 监听 PM 下单、弹出保险提示 | 1 周 |
| **P4: 杠杆合约** | LeverageVault 合约（仅赛前 + kickoff 强平）+ Session Key 授权 | 2 周 |
| **P5: Keeper 服务** | CLOB WebSocket + 5s 兜底轮询 + kickoff 强制平仓 | 1 周 |
| **P6: 风控 + 压测** | 敞口限制、资金池管理、监控告警 | 1 周 |

**总计：约 8 周**

---

## 八、信任模型总结

| 环节 | 信任谁 |
|------|--------|
| 用户私钥 | **Privy**（MPC 托管，KPAX 无法访问） |
| 保费资金 | **InsurancePool 合约**（KPAX 是 operator 但不能任意提取） |
| 杠杆仓位 | **LeverageVault 合约**（token 锁在合约里） |
| 强平决策 | **KPAX keeper**（但只能调 liquidate，且合约内置条件检查）|
| 业务账本 | **KPAX 数据库**（用于 UI 展示，链上才是最终真相）|
| 比赛结果 | **KPAX 预言机喂入**（Phase 2 可改为 UMA / Chainlink）|

相比完全中心化的托管方案：
- **合规风险大幅降低**：KPAX 不托管用户资金
- **用户信任门槛降低**：用户可以验证链上状态
- **仍保留中心化优势**：定价灵活、运营高效、开发速度快

---

## 九、与去中心化方案的关系

这个"中心化"方案本质上是**混合架构**：
- 资金层已经上链（InsurancePool + LeverageVault）
- 业务决策和风控还在后端

Phase 2 的去中心化方案是在此基础上：
- 把定价逻辑上链（或用去中心化预言机）
- 把强平改为 permissionless（任何人都可触发）
- 把比赛结果用 UMA / Kleros 等去中心化预言机
- 保险池 / 借贷池开放给外部 LP 注资
