# KPAX Ball 保险 + 杠杆 技术方案（去中心化）

> 核心思路：用户用 Privy 登录持有钱包，所有业务逻辑（定价、结算、清算）上链，KPAX 只作为前端 + Keeper。
> 用户可在链上验证所有操作，外部 LP 可注资保险池 / 借贷池。适合建立长期信任和合规。

---

## 一、系统架构

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────────────┐
│  Chrome 插件  │     │  KPAX Backend    │     │    Polygon 链上合约        │
│              │     │   (Keeper/API)    │     │                          │
├──────────────┤     ├──────────────────┤     ├──────────────────────────┤
│ Privy 登录    │     │ 业务数据查询      │     │                          │
│  └─ 嵌入钱包  │     │ (读链上状态缓存)   │     │ Privy 用户钱包 (EOA)     │
│              │     │                  │     │  · 用户自持               │
│ 查询保险池    │───►│ 读合约状态         │◄───│  · USDC 资产              │
│              │     │                  │     │                          │
│ 买保险/杠杆   │───►│ 构造交易参数 →    │───►│ InsurancePool            │
│ (Privy 签名)  │     │                  │     │  · 保费收取               │
│              │     │                  │     │  · 赛后赔付 (链上规则)     │
│              │     │                  │     │  · 外部 LP 注资接口       │
│              │     │                  │     │                          │
│              │     │                  │     │ LeverageVault            │
│              │     │                  │     │  · 开仓/平仓/强平          │
│              │     │                  │     │  · 清算条件链上判断        │
│              │     │                  │     │  · 外部 LP 借贷池         │
│              │     │ Keeper Bot:      │     │                          │
│              │     │  · 触发强平      │───►│ liquidate(id)            │
│              │     │    (任何人可触发，│     │  (permissionless)        │
│              │     │     KPAX 只是之一)│     │                          │
│              │     │  · 喂入价格      │───►│ PriceOracle              │
│              │     │                  │     │                          │
│              │     │  · 喂入比赛结果   │───►│ MatchOracle              │
│              │     │                  │     │  · UMA / Kleros 验证      │
└──────────────┘     └──────────────────┘     └──────────────────────────┘
```

**去中心化 vs 中心化的核心差异**：

| 维度 | 中心化方案 | 去中心化方案 |
|------|-----------|-------------|
| 资金托管 | 合约管理（KPAX 是 operator） | 合约管理（无单点 operator） |
| 定价 | 后端计算 | 链上计算（或带签名的定价预言机） |
| 强平权限 | 仅 KPAX keeper | 任何人都可触发（有激励） |
| 比赛结果 | KPAX 后端喂入 | UMA / Kleros 去中心化预言机 |
| 资金池 | KPAX 自有资金 | 开放给外部 LP |
| 业务账本 | KPAX 数据库（真相在链上） | 全部链上 |

---

## 二、用户登录与钱包（Privy）

### 2.1 Privy 集成

与中心化方案完全一致：

```
用户打开 KPAX → 点击"连接钱包" → Privy 登录 → MPC 创建钱包
  ↓
KPAX 只拿到：
  · Privy user ID
  · Polygon 钱包地址
  · Privy JWT
  ↓
私钥永远不经过 KPAX，用户可导出
```

### 2.2 与中心化方案的区别

| 操作 | 中心化 | 去中心化 |
|------|--------|---------|
| 登录 | Privy 钱包 | 同上 |
| 支付保费 | 用户签名 → Permit → InsurancePool | 同上（合约逻辑更复杂） |
| 开杠杆 | 用户签名 → Session key 授权 KPAX keeper | 用户签名 → 授权任何人可清算 |
| 强平 | 仅 KPAX keeper 可执行 | 任何人可执行（清算者有奖励） |
| 提现 | 用户从合约提 | 同上 |

---

## 三、智能合约体系

### 3.1 合约清单

```
contracts/
├── InsurancePool.sol          # 保险资金池（开放 LP）
├── LeverageVault.sol          # 杠杆保险柜（开放 LP）
├── MatchOracle.sol            # 比赛结果预言机（UMA 集成）
├── PriceOracle.sol            # Polymarket 价格预言机
├── PricingEngine.sol          # 链上定价引擎
├── InsuranceLPToken.sol       # 保险池 LP 凭证（ERC-20）
├── LendingLPToken.sol         # 借贷池 LP 凭证（ERC-20）
└── interfaces/
    ├── IConditionalTokens.sol
    ├── IPolymarketExchange.sol
    └── IUMA.sol               # UMA Optimistic Oracle 接口
```

### 3.2 保险合约（去中心化版）

```solidity
// InsurancePool.sol

contract InsurancePool {
    IERC20 public usdc;
    IMatchOracle public matchOracle;
    IPricingEngine public pricer;
    InsuranceLPToken public lpToken;

    struct Policy {
        address user;
        bytes32 matchId;
        uint8 insuredDirection;
        uint256 premium;
        uint256 payout;
        uint256 matchTime;
        PolicyStatus status;    // Active, SettledWin, SettledPayout, Expired
    }

    mapping(uint256 => Policy) public policies;
    uint256 public nextPolicyId;
    uint256 public totalReserves;        // 资金池总额
    uint256 public totalExposure;         // 待赔付敞口

    // ---- LP 注资 ----

    function depositLP(uint256 amount) external returns (uint256 shares) {
        usdc.transferFrom(msg.sender, address(this), amount);
        shares = (totalReserves == 0) ?
            amount :
            amount * lpToken.totalSupply() / totalReserves;
        totalReserves += amount;
        lpToken.mint(msg.sender, shares);
    }

    function withdrawLP(uint256 shares) external returns (uint256 amount) {
        amount = shares * totalReserves / lpToken.totalSupply();
        require(totalReserves - amount >= totalExposure, "Locked by exposure");
        lpToken.burn(msg.sender, shares);
        totalReserves -= amount;
        usdc.transfer(msg.sender, amount);
    }

    // ---- 用户购买保险（链上定价）----

    function purchaseInsurance(
        bytes32 matchId,
        uint8 direction,
        uint256 insuredAmount,
        uint256 coverageRatioBps
    ) external returns (uint256 policyId) {
        uint256 payout = insuredAmount * coverageRatioBps / 10000;
        uint256 premium = pricer.calculatePremium(matchId, direction, payout);

        require(totalReserves - totalExposure >= payout, "Insufficient reserves");

        usdc.transferFrom(msg.sender, address(this), premium);
        totalReserves += premium;
        totalExposure += payout;

        policies[nextPolicyId] = Policy({
            user: msg.sender,
            matchId: matchId,
            insuredDirection: direction,
            premium: premium,
            payout: payout,
            matchTime: matchOracle.getMatchTime(matchId),
            status: PolicyStatus.Active
        });

        emit PolicyCreated(nextPolicyId, msg.sender, premium, payout);
        return nextPolicyId++;
    }

    // ---- 结算（任何人可触发，无需 KPAX 介入）----

    function settle(uint256 policyId) external {
        Policy storage p = policies[policyId];
        require(p.status == PolicyStatus.Active, "Not active");

        uint8 result = matchOracle.getMatchResult(p.matchId);
        require(result != 255, "No result yet");

        totalExposure -= p.payout;

        if (result == p.insuredDirection) {
            // 用户赢了，保险不触发，保费归 LP
            p.status = PolicyStatus.SettledWin;
        } else {
            // 用户输了，赔付
            p.status = PolicyStatus.SettledPayout;
            totalReserves -= p.payout;
            usdc.transfer(p.user, p.payout);
        }

        emit PolicySettled(policyId, result);
    }
}
```

### 3.3 杠杆合约（去中心化版）

```solidity
// LeverageVault.sol

contract LeverageVault {
    IERC20 public usdc;
    IConditionalTokens public ctf;
    IPolymarketExchange public exchange;
    IPriceOracle public priceOracle;
    IMatchOracle public matchOracle;
    LendingLPToken public lpToken;

    uint256 public constant MAINTENANCE_MARGIN_BPS = 2000;    // 20%
    uint256 public constant LIQUIDATION_FEE_BPS = 200;        // 2%
    uint256 public constant LIQUIDATOR_REWARD_BPS = 50;       // 0.5% 给清算者
    uint256 public constant MAX_LEVERAGE_BPS = 50000;         // 5x

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
        PositionStatus status;
    }

    mapping(uint256 => Position) public positions;
    uint256 public nextPositionId;
    uint256 public totalLent;
    uint256 public lendingPool;

    // ---- LP 借贷资金 ----

    function depositLending(uint256 amount) external returns (uint256 shares) {
        usdc.transferFrom(msg.sender, address(this), amount);
        shares = (totalLent + lendingPool == 0) ?
            amount :
            amount * lpToken.totalSupply() / (totalLent + lendingPool);
        lendingPool += amount;
        lpToken.mint(msg.sender, shares);
    }

    // ---- 用户开仓 ----

    function openPosition(
        bytes32 matchId,
        uint256 tokenId,
        uint8 direction,
        uint256 collateral,
        uint256 leverageBps
    ) external returns (uint256 positionId) {
        require(leverageBps <= MAX_LEVERAGE_BPS);

        uint256 borrowed = collateral * (leverageBps - 10000) / 10000;
        uint256 total = collateral + borrowed;

        require(lendingPool >= borrowed, "Insufficient lending pool");

        usdc.transferFrom(msg.sender, address(this), collateral);
        lendingPool -= borrowed;
        totalLent += borrowed;

        usdc.approve(address(exchange), total);
        uint256 shares = exchange.buy(tokenId, total);

        positions[nextPositionId] = Position({
            user: msg.sender,
            matchId: matchId,
            tokenId: tokenId,
            direction: direction,
            collateral: collateral,
            borrowed: borrowed,
            shares: shares,
            entryPrice: priceOracle.getPrice(matchId, direction),
            openTime: block.timestamp,
            status: PositionStatus.Active
        });

        return nextPositionId++;
    }

    // ---- 用户主动平仓 ----

    function closePosition(uint256 positionId) external {
        Position storage pos = positions[positionId];
        require(msg.sender == pos.user && pos.status == PositionStatus.Active);
        _settlePosition(positionId, msg.sender, false);
    }

    // ---- 强制平仓（任何人可触发，有奖励）----

    function liquidate(uint256 positionId) external {
        Position storage pos = positions[positionId];
        require(pos.status == PositionStatus.Active);
        require(_isLiquidatable(positionId), "Not liquidatable");

        _settlePosition(positionId, msg.sender, true);
    }

    // ---- 赛后自动结算 ----

    function settleAfterMatch(uint256 positionId) external {
        Position storage pos = positions[positionId];
        require(pos.status == PositionStatus.Active);

        uint8 result = matchOracle.getMatchResult(pos.matchId);
        require(result != 255, "No result");

        uint256 proceeds;
        if (result == pos.direction) {
            proceeds = pos.shares;  // Polymarket 赢 = $1/share
            ctf.redeemPositions(pos.tokenId, pos.shares);
        } else {
            proceeds = 0;
        }

        _distribute(positionId, proceeds, address(0), false);
    }

    // ---- 内部函数 ----

    function _isLiquidatable(uint256 positionId) internal view returns (bool) {
        Position storage pos = positions[positionId];
        uint256 currentPrice = priceOracle.getPrice(pos.matchId, pos.direction);
        uint256 positionValue = pos.shares * currentPrice / 10000;
        uint256 interest = _calculateInterest(pos);
        uint256 debt = pos.borrowed + interest;
        uint256 threshold = debt * (10000 + MAINTENANCE_MARGIN_BPS) / 10000;
        return positionValue <= threshold;
    }

    function _settlePosition(
        uint256 positionId,
        address liquidator,
        bool isLiquidation
    ) internal {
        Position storage pos = positions[positionId];
        uint256 proceeds = exchange.sell(pos.tokenId, pos.shares);
        _distribute(positionId, proceeds, liquidator, isLiquidation);
    }

    function _distribute(
        uint256 positionId,
        uint256 proceeds,
        address liquidator,
        bool isLiquidation
    ) internal {
        Position storage pos = positions[positionId];
        uint256 interest = _calculateInterest(pos);
        uint256 debt = pos.borrowed + interest;

        uint256 liquidatorReward = 0;
        uint256 protocolFee = 0;

        if (isLiquidation) {
            liquidatorReward = proceeds * LIQUIDATOR_REWARD_BPS / 10000;
            protocolFee = proceeds * (LIQUIDATION_FEE_BPS - LIQUIDATOR_REWARD_BPS) / 10000;
        }

        uint256 kpaxReceives = debt + protocolFee;
        if (kpaxReceives + liquidatorReward > proceeds) {
            kpaxReceives = proceeds > liquidatorReward ? proceeds - liquidatorReward : 0;
        }

        uint256 userReceives = proceeds > kpaxReceives + liquidatorReward
            ? proceeds - kpaxReceives - liquidatorReward : 0;

        // 还款到借贷池
        lendingPool += pos.borrowed;
        totalLent -= pos.borrowed;

        // 清算者奖励
        if (liquidatorReward > 0 && liquidator != address(0)) {
            usdc.transfer(liquidator, liquidatorReward);
        }

        // 用户收回剩余
        if (userReceives > 0) {
            usdc.transfer(pos.user, userReceives);
        }

        pos.status = isLiquidation
            ? PositionStatus.Liquidated
            : PositionStatus.Closed;
    }

    function _calculateInterest(Position storage pos) internal view returns (uint256) {
        uint256 daysHeld = (block.timestamp - pos.openTime) / 86400 + 1;
        return pos.borrowed * daysHeld / 1000;  // 0.1% 每天
    }
}
```

### 3.4 预言机合约

#### 3.4.1 MatchOracle（比赛结果预言机）

初期由 KPAX keeper 喂入，后期迁移到 UMA / Kleros：

```solidity
// MatchOracle.sol

contract MatchOracle {
    IUMA public uma;                // UMA Optimistic Oracle
    address public kpaxKeeper;       // 初期由 KPAX 喂入

    struct MatchData {
        uint256 matchTime;
        uint8 result;                // 0=home, 1=draw, 2=away, 255=未出
        bool kpaxSubmitted;
        bool umaDisputed;
        bool finalized;
    }

    mapping(bytes32 => MatchData) public matches;

    // KPAX 先喂入（快速）
    function submitResultByKpax(bytes32 matchId, uint8 result) external {
        require(msg.sender == kpaxKeeper);
        require(!matches[matchId].kpaxSubmitted);
        matches[matchId].result = result;
        matches[matchId].kpaxSubmitted = true;

        // 同时提交到 UMA 挑战窗口（24h）
        uma.requestPrice(matchId, ...);
    }

    // UMA 挑战期结束后确认
    function finalizeResult(bytes32 matchId) external {
        require(matches[matchId].kpaxSubmitted);
        uint256 umaResult = uma.settleAndGetPrice(matchId);

        if (umaResult != matches[matchId].result) {
            // 有争议，以 UMA 为准
            matches[matchId].result = uint8(umaResult);
            matches[matchId].umaDisputed = true;
        }

        matches[matchId].finalized = true;
    }

    function getMatchResult(bytes32 matchId) external view returns (uint8) {
        return matches[matchId].finalized ? matches[matchId].result : 255;
    }
}
```

#### 3.4.2 PriceOracle（Polymarket 价格预言机）

```solidity
// PriceOracle.sol

contract PriceOracle {
    address public keeper;
    mapping(bytes32 => mapping(uint8 => uint256)) public prices;  // matchId → direction → price (basis points)
    mapping(bytes32 => uint256) public lastUpdate;

    // Keeper 每分钟喂入价格
    function updatePrices(bytes32 matchId, uint256[3] calldata probs) external onlyKeeper {
        for (uint8 i = 0; i < 3; i++) {
            prices[matchId][i] = probs[i];
        }
        lastUpdate[matchId] = block.timestamp;
    }

    function getPrice(bytes32 matchId, uint8 direction) external view returns (uint256) {
        require(block.timestamp - lastUpdate[matchId] < 5 minutes, "Stale price");
        return prices[matchId][direction];
    }
}
```

### 3.5 链上定价引擎

```solidity
// PricingEngine.sol

contract PricingEngine {
    IPriceOracle public priceOracle;

    function calculatePremium(
        bytes32 matchId,
        uint8 direction,
        uint256 payout
    ) external view returns (uint256) {
        uint256 marketProb = priceOracle.getPrice(matchId, direction);  // 基点
        uint256 lossProb = 10000 - marketProb;

        // premium = payout × lossProb × profitMargin
        // profitMargin = 1.3 (30%)
        return payout * lossProb * 13 / (10000 * 10);
    }
}
```

---

## 四、后端角色（Keeper + 缓存 + 前端服务）

去中心化方案中，后端不再管理资金和账本，只做：

### 4.1 Keeper（自动化操作）

```python
# keeper.py

async def run_keeper():
    while True:
        # 1. 喂入价格到 PriceOracle
        for match in active_matches:
            probs = await fetch_polymarket_probs(match.slug)
            price_oracle.updatePrices(match.id, probs)

        # 2. 喂入比赛结果
        for match in finished_matches:
            if not match_oracle.isSubmitted(match.id):
                match_oracle.submitResultByKpax(match.id, match.result)

        # 3. 触发强平（任何人都可以做，KPAX 只是之一）
        for pos_id in active_positions:
            if leverage_vault.isLiquidatable(pos_id):
                leverage_vault.liquidate(pos_id)
                # 赚取 0.5% 清算奖励

        # 4. 触发赛后结算
        for pos_id in settle_eligible_positions:
            leverage_vault.settleAfterMatch(pos_id)

        for policy_id in settle_eligible_policies:
            insurance_pool.settle(policy_id)

        await asyncio.sleep(60)
```

### 4.2 链上事件缓存（加速查询）

后端订阅合约事件，存入数据库，供前端快速查询（避免每次都读链）：

```python
async def cache_events():
    insurance_pool.events.PolicyCreated.subscribe(on_policy_created)
    insurance_pool.events.PolicySettled.subscribe(on_policy_settled)
    leverage_vault.events.PositionOpened.subscribe(on_position_opened)
    leverage_vault.events.PositionLiquidated.subscribe(on_position_liquidated)
    # ... 其他事件
```

### 4.3 前端服务 API

```
GET /api/insurance/quote?matchId=...     # 链上读取当前报价
GET /api/insurance/policies?user=...     # 读数据库缓存的保单
GET /api/leverage/positions?user=...      # 读数据库缓存的仓位
POST /api/match/register                  # 注册新比赛到 MatchOracle（KPAX keeper 权限）
```

---

## 五、LP（流动性提供者）机制

去中心化方案的核心优势：**资金池开放**

### 5.1 保险池 LP

```
任何人可以往 InsurancePool 注入 USDC → 获得 LP 凭证
  ↓
LP 承担赔付风险，分享保费收入
  ↓
年化收益 = 总保费收入 / 总资金池
  ↓
示例：
  · 资金池 $100,000
  · 月保费收入 $8,000
  · 月赔付支出 $5,000
  · 月净收入 $3,000
  · 年化 ≈ 36%
```

### 5.2 借贷池 LP

```
任何人可以往 LeverageVault 注入 USDC → 获得借贷凭证
  ↓
LP 借出资金，收取利息（0.1%/天 = 36.5%/年）
  ↓
强平机制保护 LP 本金
  ↓
如果强平不及时导致损失，由 KPAX 承担（作为 first loss buffer）
```

---

## 六、实施计划

| 阶段 | 内容 | 时间 |
|------|------|------|
| **P1: 合约开发** | InsurancePool + PricingEngine + MatchOracle | 4 周 |
| **P2: Privy 集成** | 前端钱包对接 | 1 周 |
| **P3: 合约测试** | Hardhat 单元测试 + Polygon Mumbai 测试网 | 2 周 |
| **P4: 安全审计** | 第三方审计（至少 1 家） | 2-4 周 |
| **P5: Keeper + 缓存** | 后端自动化 + 事件订阅 | 2 周 |
| **P6: 前端集成** | 插件对接合约 | 2 周 |
| **P7: 杠杆合约** | LeverageVault + PriceOracle | 4 周 |
| **P8: 杠杆测试 + 审计** | 测试 + 审计 | 3 周 |
| **P9: UMA 集成** | MatchOracle 升级到去中心化预言机 | 2 周 |

**总计：约 22-24 周**

---

## 七、与中心化方案的关系

```
中心化方案（9 周）：
  · 基础设施：Privy 登录 + 简单合约池 + KPAX keeper
  · 已经实现了"非托管"（资金在合约里）
  · 但定价、强平、结算仍然中心化

去中心化方案（22 周）：
  · 在中心化方案基础上升级：
    - 定价上链
    - 强平开放（任何人可触发）
    - UMA 去中心化比赛结果预言机
    - LP 开放（外部注资）
  · 审计成本 $50K-$100K
```

**推荐路径**：
1. **先做中心化方案（9 周上线）**：快速验证市场需求、积累数据
2. **数据验证后再做去中心化升级**：有信心投入审计成本
3. **合约设计留好升级空间**：中心化方案的 `InsurancePool` 和 `LeverageVault` 未来可以接入 LP 模块

---

## 八、信任模型总结

| 环节 | 信任谁 |
|------|--------|
| 用户私钥 | **Privy**（MPC 托管，任何人都无法单独动用） |
| 资金 | **链上合约**（代码公开，审计后不可篡改） |
| 定价 | **链上 PricingEngine** + **PriceOracle**（KPAX 喂入价格有延迟窗口，用户可看到） |
| 强平 | **合约内置条件**（任何人可触发，有经济激励）|
| 比赛结果 | **MatchOracle**（初期 KPAX 喂入 + UMA 挑战机制）|
| LP 资金 | **智能合约锁仓**（按比例分配收益）|

KPAX 的收益来源：
- 清算奖励（作为快速清算者之一）
- 协议费分成（每笔保险 / 杠杆的一小部分）
- 资金池的 first loss buffer 收益（承担额外风险换高回报）
