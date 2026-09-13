# KPAX Lending 开发计划

> **状态**：Plan v0.1
> **作者**：Eng Lead (KPAX)
> **日期**：2026-04-23
> **依赖**：`docs/kpax-lending-prd.md`（PRD）、`docs/tech-plan-centralized.md`（中心化架构）、`docs/polymargin-research.md`（机制参考）
> **总周期**：12 周（6 周 MVP + 3 周 Alpha + 3 周 Beta）

---

## 0. 摘要

**目标**：在 12 周内交付可公测的 KPAX Lending MVP，允许用户抵押 Polymarket 足球 CTF token 借出 USDC。

**交付物**：
1. `LendingVault.sol`（Polygon，上线前审计）
2. 后端 Lending API + Keeper 服务
3. 插件 Side Panel 内的借贷入口 + Dashboard
4. AI 风控模块（复用 `quick_preview` 调用链）
5. 运维监控 + 灰度上线流程

**关键依赖（已就位）**：
- ✅ Privy 登录 + MPC 钱包（Sprint 0 已完成）
- ✅ Polymarket API 封装（`polymarket-api.ts`）
- ✅ 后端 FastAPI + SQLAlchemy 2.0 + Alembic
- ✅ AI 调用框架（`ai_provider.py`、`quick_preview.py`）
- ✅ Chrome 插件 Side Panel + 个人页骨架

**关键风险**：
- 🔴 **合约审计排期**：至少预留 2 周（Week 7-8），否则公测延期
- 🟡 **Polymarket CTF 标准兼容性**：ERC-1155 `safeTransferFrom` 需要对应的 `onERC1155Received` 实现
- 🟡 **Keeper 在强平高峰期的 gas 竞拍**：需要准备 gas 策略 + 重试逻辑
- 🟡 **LP 初始资金**：Treasury $50K，上线后根据 utilization 动态补充

---

## 1. 范围与非范围

### 1.1 MVP 范围（Phase 1）

按 PRD §5.1 定稿，Sprint 计划必做：

| # | 模块 | 责任层 |
|---|------|-------|
| M1 | 持仓导入（读 Polymarket API） | 前端 + 后端 |
| M2 | Terms of Service 签署（D8） | 前端 + 后端 |
| M3 | 抵押借款（CTF → Vault, USDC → User） | 合约 + 后端 + 前端 |
| M4 | AI 借款建议（LTV 推荐 + 风险评分） | 后端 |
| M5 | 还款（全额 / 部分，仅 Privy 钱包 USDC） | 合约 + 后端 + 前端 |
| M6 | 仓位看板 + 实时 LTV | 前端 + 后端 |
| M7 | Keeper 清算（LTV > 80% 或 kickoff-2h） | 后端 + 合约 |
| M8 | 预警（70% / 80% / kickoff-4h / -2h） | 后端 + 插件通知 |
| M9 | LP 池（KPAX Treasury 单边） | 合约 + 后端 |
| M10 | 埋点 + 监控仪表盘 | 后端 + 运维 |

### 1.2 明确不做（砍到 Phase 2+）

按 PRD §5.2：外部 LP、多 token 组合抵押、动态利率、非体育市场、in-play 借款、追加保证金、Solana 跨链、Mobile App、$KPAX 代币。

### 1.3 Sprint 范围收紧原则

若任一 Sprint 延期超过 3 天，优先砍以下功能以保 MVP 核心：
1. AI 建议深度解释（可退化为"按联赛档位给固定 LTV"）
2. 后验分析总结（Phase 1.5 再做）
3. 借款历史页（仅展示 Active Loans，History 延期）
4. 邮件告警（仅保留 Chrome 推送）

---

## 2. 技术架构总览

### 2.1 分层视图

```
┌───────────────────────────────────────────────────────────────┐
│ 插件 Side Panel (React)                                        │
│  · BorrowEntry（比赛页浮层入口）                                │
│  · LendingDashboard（仓位 + LTV + 倒计时）                      │
│  · BorrowFlow（AI 建议 + 金额选择 + Privy 签名）                │
│  · TosModal（首次弹出）                                         │
│  · Chrome 通知（70% / 80% / kickoff 预警）                      │
└──────────────────────┬────────────────────────────────────────┘
                       │ HTTPS + KPAX JWT
┌──────────────────────▼────────────────────────────────────────┐
│ 后端 FastAPI                                                    │
│  /api/lending/*                                                │
│   ├── positions       当前持仓 + 可借额                         │
│   ├── risk-assessment AI 风控建议                               │
│   ├── prepare-borrow  构造 UserOperation                        │
│   ├── confirm-borrow  监听链上事件、落库                        │
│   ├── prepare-repay                                            │
│   ├── loans           我的借款列表                              │
│   └── tos             签署 + 查询状态                            │
│                                                                │
│ 后台服务（常驻）                                                │
│  ├── LendingKeeper     30s 扫描 LTV + kickoff 清算              │
│  ├── PriceWatcher      CLOB WebSocket 实时价格                  │
│  ├── AlertEngine       70% / 80% / kickoff 预警                 │
│  └── EventIndexer      LendingVault 事件落库                    │
└──────────────────────┬────────────────────────────────────────┘
                       │ RPC (Alchemy/QuickNode)
┌──────────────────────▼────────────────────────────────────────┐
│ Polygon 链上                                                    │
│  ├── LendingVault.sol      托管 CTF + 放 USDC                   │
│  ├── USDC (0x3c499c...)    Polygon Native                      │
│  └── Polymarket CTF        ERC-1155                            │
│                                                                │
│  外部集成：Polymarket Exchange（清算时卖仓位）                   │
└───────────────────────────────────────────────────────────────┘
```

### 2.2 关键设计决策

| # | 决策 | 方案 | 理由 |
|---|------|------|------|
| T1 | 合约托管模型 | **Non-custodial Vault**，KPAX 只持有 keeper 权限 | PRD §6.5 已定稿 |
| T2 | 借款计息 | **按秒累计、简单利息、不复利** | PRD §8.2；实现简单，可预测 |
| T3 | 价格预言机 | **Polymarket CLOB mid price** + 5 分钟 TWAP | 本地部署，无预言机依赖；TWAP 避免瞬时操纵 |
| T4 | 强平路径 | **Vault 调 Polymarket Exchange 直接卖出** | 避免中间合约，减少攻击面 |
| T5 | 用户授权 | `ERC1155.setApprovalForAll` 一次性授权 Vault | 后续借款复用，避免每次签名 |
| T6 | 开仓交易批量 | **ERC-4337 UserOperation 合并 approve + open** | Privy 原生支持；用户只签 1 次 |
| T7 | LP 资金来源 | KPAX Treasury 单钱包注资，合约记账 | 不开放外部 LP，简化 MVP |
| T8 | 合约升级 | 2-of-3 multisig + 48h timelock | PRD §6.5 |
| T9 | 数据库事务 | **链上优先**：Keeper 等合约事件触发后才更新 DB `status` | 避免 DB/链不一致 |
| T10 | 关闭借款的最早时间 | 比赛开赛前至少 **24 小时** | PRD §8.5 |

### 2.3 与现有代码库的融合

复用清单：
- `app/auth.py` → `current_user` 依赖；无需改动
- `app/services/quick_preview.py` → AI 调用模板；复制到 `lending_risk_assessor.py`，换 prompt
- `app/services/ai_provider.py` → 复用 LLM dispatch
- `extension/src/shared/wallet.ts` → 复用 USDC 余额查询；新增 CTF balance、Vault ABI 调用
- `extension/src/shared/polymarket-api.ts` → 复用市场信息；新增 `getUserPositions`、`getMarketDepth`

新增文件清单（见 §3-§7）。

---

## 3. 数据模型

### 3.1 新增表

**`loans`** — 借款核心表

```python
class Loan(Base):
    __tablename__ = "loans"

    id: int                              # PK
    user_id: int                         # FK → users.id
    wallet_address: str                  # 冗余，加速查询

    # 抵押物
    ctf_token_id: str                    # Polymarket CTF tokenId (uint256 as string)
    market_slug: str                     # 比赛识别
    home_team: str
    away_team: str
    competition: str
    collateral_shares: Decimal           # CTF 份额（例：833）
    collateral_value_at_open: Decimal    # 开仓时估值（USD）
    entry_price: Decimal                 # 开仓时单价

    # 借款参数
    principal: Decimal                   # 借款本金（USDC）
    apr_bps: int                         # 年化利率（基点，MVP=1200）
    league_tier: int                     # 1 / 2 / 3（影响 LTV 上限）
    opened_ltv: Decimal                  # 开仓时 LTV

    # 状态
    status: str                          # active | repaid | liquidated_ltv | liquidated_kickoff | force_closed
    onchain_loan_id: int                 # LendingVault 合约的 loanId
    open_tx_hash: str
    close_tx_hash: str | None

    # 时间轴
    opened_at: datetime
    match_kickoff_at: datetime           # 用于 kickoff-2h 触发
    closed_at: datetime | None

    # 结算快照（仅在 closed 后填）
    total_interest_paid: Decimal | None
    liquidation_penalty: Decimal | None
    residual_to_user: Decimal | None     # 强平后剩余 USDC
    exit_price: Decimal | None

    # AI 元数据（用于回训）
    ai_recommended_ltv: Decimal | None
    ai_risk_score: int | None
    ai_accepted: bool                    # 用户是否采纳 AI 建议
```

索引：
- `(user_id, status)`：用户仓位查询
- `(status, match_kickoff_at)`：Keeper 扫描 kickoff-2h
- `(status, ctf_token_id)`：Keeper 扫描 LTV（分组查 token 价格）

**`lending_tos_acceptance`** — ToS 签署记录

```python
class TosAcceptance(Base):
    __tablename__ = "lending_tos_acceptance"

    id: int
    user_id: int                         # FK → users.id
    tos_version: str                     # 如 "v1.0"
    signature: str                       # 客户端记录的签署确认（MVP 存 "clicked"）
    accepted_at: datetime
```

**`lending_events`** — 链上事件落库（审计 + 回查）

```python
class LendingEvent(Base):
    __tablename__ = "lending_events"

    id: int
    loan_id: int | None                  # FK → loans.id (可能为 null, 如 LPDeposit)
    event_type: str                      # LoanOpened | Repaid | Liquidated | LPDeposit | LPWithdraw
    block_number: int
    tx_hash: str
    log_index: int
    data: dict                           # JSONB raw args
    indexed_at: datetime
```

唯一索引：`(tx_hash, log_index)`

**`lending_alerts`** — 预警发送幂等（避免重复通知）

```python
class LendingAlert(Base):
    __tablename__ = "lending_alerts"

    id: int
    loan_id: int
    alert_type: str                      # ltv_70 | ltv_80 | kickoff_4h | kickoff_2h
    sent_at: datetime

    # 唯一：(loan_id, alert_type)
```

### 3.2 Migration 清单

- `alembic revision -m "add_loans_table"` → loans
- `alembic revision -m "add_lending_tos"` → lending_tos_acceptance
- `alembic revision -m "add_lending_events"` → lending_events
- `alembic revision -m "add_lending_alerts"` → lending_alerts

**注意**：按之前的教训（autogenerate 出空 migration），新表**必须**：
1. 在 `app/models/__init__.py` 显式 import 新 model
2. 在 `alembic/env.py` 的 `target_metadata` 看到这些 model
3. autogenerate 后**人工检查**一遍再 upgrade

---

## 4. 智能合约设计

### 4.1 合约清单

| 合约 | 职责 | 状态 |
|------|------|------|
| `LendingVault.sol` | 核心：托管 CTF、放贷 USDC、清算 | **新建** |
| `LendingVaultProxy.sol` | UUPS / Transparent Proxy（为将来升级） | **新建** |
| `Timelock.sol` | 48h timelock + 2-of-3 multisig owner | 复用 OpenZeppelin |

### 4.2 `LendingVault.sol` 接口草案

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC1155Receiver} from "@openzeppelin/contracts/token/ERC1155/IERC1155Receiver.sol";

interface IPolymarketExchange {
    function sell(uint256 tokenId, uint256 shares) external returns (uint256 usdcOut);
}

contract LendingVault is IERC1155Receiver {
    // ---- 存储 ----
    struct Loan {
        address borrower;
        uint256 ctfTokenId;
        uint256 collateralShares;
        uint256 principal;         // USDC 本金
        uint256 openTime;
        uint256 matchKickoff;      // 从后端写入
        uint8 leagueTier;          // 1/2/3
        bool active;
    }

    mapping(uint256 => Loan) public loans;
    uint256 public nextLoanId;

    IERC20 public immutable usdc;
    address public immutable ctf;              // Polymarket ConditionalTokens
    IPolymarketExchange public immutable exchange;
    address public keeper;                      // KPAX Keeper EOA / Multisig
    address public admin;                       // Multisig + Timelock

    uint256 public lpPoolBalance;

    uint256 public constant APR_BPS = 1200;    // 12%
    uint256 public constant LIQUIDATION_PENALTY_BPS = 200;  // 2%
    uint256 public constant SECONDS_PER_YEAR = 31_536_000;

    // ---- 事件 ----
    event LoanOpened(uint256 indexed loanId, address indexed borrower, uint256 ctfTokenId, uint256 shares, uint256 principal, uint8 leagueTier);
    event LoanRepaid(uint256 indexed loanId, uint256 principalPaid, uint256 interestPaid);
    event LoanLiquidated(uint256 indexed loanId, string reason, uint256 proceeds, uint256 residualToUser);
    event LPDeposit(address indexed from, uint256 amount);
    event LPWithdraw(address indexed to, uint256 amount);

    // ---- Borrower 接口 ----
    function openLoan(
        uint256 ctfTokenId,
        uint256 shares,
        uint256 principal,
        uint256 matchKickoff,
        uint8 leagueTier
    ) external returns (uint256 loanId);

    function repay(uint256 loanId, uint256 amount) external;     // amount=0 表示全额
    function closeAndWithdraw(uint256 loanId) external;          // 还清后取回抵押

    // ---- Keeper 接口 ----
    function liquidate(uint256 loanId, string calldata reason) external onlyKeeper;

    // ---- LP 接口 ----
    function depositLP(uint256 amount) external onlyAdmin;
    function withdrawLP(uint256 amount) external onlyAdmin;

    // ---- 视图 ----
    function debtOf(uint256 loanId) external view returns (uint256 totalDebt, uint256 interest);
    function isLiquidatable(uint256 loanId, uint256 currentPrice) external view returns (bool, string memory);

    // ---- 管理 ----
    function setKeeper(address newKeeper) external onlyAdmin;
    function pause() external onlyAdmin;  // 紧急停止新借款

    // IERC1155Receiver: 接受 CTF 转入
    function onERC1155Received(address, address, uint256, uint256, bytes calldata) external returns (bytes4);
}
```

### 4.3 清算前置条件（合约内强制）

Keeper 调 `liquidate` 时，合约必须满足以下之一：
1. `block.timestamp >= loan.matchKickoff - 2 hours`
2. `currentPrice * shares <= debt * 10_000 / 8_000`（LTV ≥ 80%）

`currentPrice` 从 Chainlink Keeper 或 Oracle 读入（MVP 暂由 Keeper 传参，合约内做 sanity check）。

**❗ 风险点**：Keeper 传错价可以用"合约内上下限"防御：
- 上限：开仓价 × 2
- 下限：开仓价 × 0.1
- 超出 → revert，触发告警

### 4.4 审计与测试策略

| 环节 | 方案 |
|------|------|
| 单测覆盖率 | Foundry，目标 100% 分支 |
| 属性测试 | Foundry `forge fuzz` — 不变性：`lpPoolBalance == 余额 - sum(principals)` |
| 形式化 | 不强求 MVP，Phase 2 考虑 Certora |
| 第三方审计 | **必须**：Week 7 前签约，目标 OpenZeppelin / ConsenSys Diligence，预算 $10-20K |
| 漏洞赏金 | Immunefi，$5K 池子，Week 9 启动 |

---

## 5. 后端 API 设计

### 5.1 路由清单（`app/routers/lending.py`）

```
GET  /api/lending/positions              # 用户 Polymarket 体育持仓 + 可借额
POST /api/lending/risk-assessment        # AI 推荐 LTV/借款额
POST /api/lending/prepare-borrow         # 返回 UserOperation payload 供 Privy 签名
POST /api/lending/confirm-borrow         # 上报 tx_hash，后端等事件 + 落库
GET  /api/lending/loans                  # 我的借款列表
GET  /api/lending/loans/{id}             # 借款详情 + 实时 LTV
POST /api/lending/prepare-repay          # 构造还款 tx
POST /api/lending/confirm-repay          # 上报还款 tx_hash
GET  /api/lending/tos                    # 查询当前 TOS 版本 + 是否已签署
POST /api/lending/tos/accept             # 签署
GET  /api/lending/config                 # LTV 分档 / APR / Kickoff buffer 参数
```

### 5.2 Service 清单（`app/services/lending/`）

- `pricing.py` — 当前 LTV 计算、利息累积
- `polymarket_positions.py` — 抓取用户 CTF 持仓 + 联赛分档
- `league_tier.py` — 联赛 → Tier 映射（硬编码初版）
- `risk_assessor.py` — AI 风控（复用 `ai_provider.py`）
- `vault_client.py` — Web3 合约调用封装（web3.py）
- `event_indexer.py` — 后台任务：订阅 Vault 事件
- `keeper.py` — 后台任务：30s 扫描 + 清算触发
- `price_watcher.py` — CLOB WebSocket 订阅
- `alert_engine.py` — 70% / 80% / kickoff 推送

### 5.3 关键流程伪码

**POST `/api/lending/prepare-borrow`**

```python
async def prepare_borrow(req: BorrowRequest, user=Depends(current_user)):
    # 1. 校验
    position = await get_user_position(user.wallet_address, req.ctf_token_id)
    assert position.shares >= req.collateral_shares

    tier = league_tier_for(position.market_slug)
    max_ltv = LTV_CONFIG[tier].max
    assert req.principal / (position.shares * position.price) <= max_ltv

    # 2. 校验 ToS
    assert await tos_signed(user.id, current_tos_version())

    # 3. 校验 kickoff 缓冲（>= 24h）
    match = await get_match(position.market_slug)
    assert match.kickoff > now + 24h

    # 4. 构造 UserOperation
    calls = [
        # setApprovalForAll（若未授权）
        ctf.encode("setApprovalForAll", vault_addr, True),
        # openLoan
        vault.encode("openLoan",
            req.ctf_token_id, req.collateral_shares,
            req.principal, match.kickoff_timestamp, tier),
    ]
    user_op = await build_user_operation(user.wallet_address, calls)

    return {"user_operation": user_op, "estimated_gas": ..., "quote_expiry": ...}
```

**POST `/api/lending/confirm-borrow`**

```python
async def confirm_borrow(req: ConfirmRequest, user=Depends(current_user)):
    # 1. 等 tx 上链（or 立即返回 pending，让 EventIndexer 处理）
    receipt = await wait_tx(req.tx_hash, timeout=30)
    assert receipt.status == 1

    # 2. 解析 LoanOpened 事件
    event = parse_loan_opened(receipt)

    # 3. 落库（UPSERT，幂等）
    loan = Loan(
        user_id=user.id,
        onchain_loan_id=event.loan_id,
        ctf_token_id=event.ctf_token_id,
        collateral_shares=event.shares,
        principal=event.principal,
        opened_at=block_timestamp(receipt.block_number),
        ...
    )
    db.add(loan)
    await db.commit()

    # 4. 落 lending_events（幂等去重）
    ...

    return {"loan_id": loan.id}
```

### 5.4 利息计算（Python 端一致）

```python
def current_debt(loan: Loan, now: datetime) -> Decimal:
    elapsed = (now - loan.opened_at).total_seconds()
    interest = loan.principal * loan.apr_bps / 10_000 * elapsed / 31_536_000
    return loan.principal + Decimal(interest).quantize(Decimal("0.000001"))
```

合约 `debtOf` 和 Python `current_debt` 必须 bit-for-bit 一致（同样的舍入）。单测要跨语言对齐。

---

## 6. 前端组件设计

### 6.1 新增文件

```
extension/src/
├── shared/
│   ├── lending-api.ts          # 调 /api/lending/*
│   ├── vault-abi.ts            # LendingVault ABI + addr
│   └── league-tiers.ts         # 联赛分档配置（缓存/api/lending/config）
├── sidepanel/
│   └── components/
│       ├── BorrowEntry.tsx     # 比赛页浮层入口
│       ├── TosModal.tsx
│       ├── BorrowFlow/
│       │   ├── index.tsx
│       │   ├── AmountStep.tsx  # AI 建议 + 滑块
│       │   ├── ConfirmStep.tsx
│       │   └── SigningStep.tsx
│       ├── LendingDashboard.tsx
│       ├── LoanCard.tsx
│       ├── LtvBar.tsx
│       └── RepayModal.tsx
└── service-worker/
    └── lending-notifications.ts  # Chrome 推送处理
```

### 6.2 状态与路由

`App.tsx` 扩展 `ViewState`:

```ts
type ViewState =
  | { view: "home" }
  | { view: "profile" }
  | { view: "lending-dashboard" }
  | { view: "borrow-flow"; ctfTokenId: string }
  | { view: "loan-detail"; loanId: string };
```

个人页 "Products → Lending" 入口改为跳 `lending-dashboard`。

### 6.3 AI 建议 UI（PRD §7.4）

组件：`<AiSuggestionCard />`，展开/折叠两态。默认折叠显示 1 行 "Recommended $250 (50%)"；展开显示风险因子、相似仓位表现。数据来自 `POST /api/lending/risk-assessment`。

### 6.4 Chrome 推送实现

- Service Worker 订阅后端 `/api/lending/alerts/subscribe`（用 Web Push 或轮询）
- MVP 第一版用**轮询**：SW 每 60s 拉一次 `/api/lending/alerts/pending`，触发 `chrome.notifications.create`
- 第二版切 Web Push（需要 VAPID key 配置）

### 6.5 i18n 新增

预估新增约 50 条 i18n key（`lending*` 前缀），在 `shared/i18n.ts` 一次性加齐。

---

## 7. Keeper 服务设计

### 7.1 总体

三个后台协程，都在 FastAPI lifespan 内启动：

```python
# app/main.py lifespan
async def lifespan(app):
    tasks = [
        asyncio.create_task(event_indexer_loop()),
        asyncio.create_task(price_watcher_loop()),
        asyncio.create_task(keeper_loop()),
        asyncio.create_task(alert_engine_loop()),
    ]
    yield
    for t in tasks: t.cancel()
```

### 7.2 `keeper_loop`

```python
async def keeper_loop():
    while True:
        try:
            now = datetime.utcnow()

            # 1. Kickoff-2h 扫描
            due_kickoff = await db.execute(
                select(Loan).where(
                    Loan.status == "active",
                    Loan.match_kickoff_at <= now + timedelta(hours=2),
                )
            )
            for loan in due_kickoff:
                await trigger_liquidation(loan, reason="kickoff_2h")

            # 2. LTV 扫描
            active_loans = await get_active_loans()
            prices = await batch_fetch_prices([l.ctf_token_id for l in active_loans])
            for loan in active_loans:
                ltv = current_ltv(loan, prices[loan.ctf_token_id])
                if ltv >= 0.80:
                    await trigger_liquidation(loan, reason="ltv_80")

        except Exception as e:
            logger.exception("keeper error")
            await sentry_capture(e)

        await asyncio.sleep(30)
```

### 7.3 `trigger_liquidation` 幂等

```python
async def trigger_liquidation(loan, reason):
    # 幂等锁
    async with db.begin():
        row = await db.execute(
            select(Loan).where(Loan.id == loan.id).with_for_update()
        )
        if row.status != "active":
            return  # 已被其他进程触发

        # 预估 gas，设置 tipCap
        tx = await vault.functions.liquidate(loan.onchain_loan_id, reason).build_transaction(...)
        signed = sign_with_keeper_key(tx)
        tx_hash = await w3.eth.send_raw_transaction(signed)

        # 标记 pending（实际 status 等事件确认）
        loan.status = "liquidating"
        loan.close_tx_hash = tx_hash
    # EventIndexer 收到 LoanLiquidated 后会把 status 改成 liquidated_*
```

### 7.4 Gas 策略

- 使用 **EIP-1559** `maxPriorityFeePerGas`：基础 +30 gwei
- 发送 60s 后仍 pending → 替换交易，tip 翻倍
- 若连续 3 笔失败 → PagerDuty 告警 + 降级（禁用新借款）

### 7.5 Price Watcher

MVP 第一版：REST 轮询 CLOB `/prices-history`，每 10s 一次。
Phase 1.5 升级到 CLOB WebSocket（和 Leverage keeper 共享实现）。

---

## 8. AI 风控模块

### 8.1 输入

```json
{
  "market_slug": "premier-league-man-city-champions",
  "ctf_token_id": "123...",
  "user_shares": 833,
  "current_price": 0.60,
  "competition": "Premier League",
  "kickoff_at": "2026-04-30T14:00:00Z"
}
```

### 8.2 输出（PRD §9.1）

```json
{
  "recommended_borrow_usd": 250,
  "recommended_ltv": 0.50,
  "risk_score": 2,
  "risk_reasoning": "…",
  "liquidation_probability_estimate": 0.042,
  "key_risks": [...],
  "similar_historical_loans": {"count": 10, "avg_liquidation_rate": 0.042}
}
```

### 8.3 实现

- Copy `services/quick_preview.py` → `services/lending/risk_assessor.py`
- 新 prompt 在 `prompts/lending_risk.md`
- 输入增补：联赛 tier、历史价格波动、距开赛时长
- 历史相似仓位：上线初期用**硬编码的锚点示例**，待数据积累后从 DB 查询

### 8.4 降级

AI 调用失败 / 超时（> 3s）→ 用**静态 LTV**：league_tier 的默认 LTV - 10%，风险文案用预置模板。埋点 `ai_fallback_used`。

---

## 9. Sprint 计划（12 周）

每 Sprint 2 周。按 PRD §13 对齐，加入具体 issue 级任务。

### Sprint 1 · Week 1-2：合约 + 数据层

**目标**：合约雏形、DB 就位、`getUserPositions` 能跑。

**任务**：
- [ ] [合约] 初始化 Foundry 项目 `contracts/`，copy OZ 依赖
- [ ] [合约] 实现 `LendingVault.sol` 基本功能（openLoan / repay / liquidate / depositLP）
- [ ] [合约] 单测：开仓/还款/清算路径 20+ 用例
- [ ] [合约] Mumbai testnet 首次部署
- [ ] [后端] `app/models/loan.py`、`tos_acceptance.py`、`lending_event.py`、`lending_alert.py`
- [ ] [后端] Alembic migration × 4（**手写，不 autogenerate**）
- [ ] [后端] `GET /api/lending/config`（返回 LTV 档位、APR）
- [ ] [后端] `GET /api/lending/positions`（抓用户 Polymarket CTF 持仓 + 估值）
- [ ] [后端] `POST /api/lending/tos/accept`、`GET /api/lending/tos`
- [ ] [前端] `BorrowEntry` 比赛页浮层骨架（仅入口，不接流程）
- [ ] [前端] `TosModal` 组件 + 首次弹窗逻辑
- [ ] [i18n] 新增 lending 前缀 key 第 1 批（入口 + ToS）

**Sprint 1 Done Gate**：
- 测试网能 call `openLoan` → 看到 `LoanOpened` 事件
- 后端能列出用户的 Polymarket 持仓
- 插件能弹 ToS 模态框并签署

### Sprint 2 · Week 3-4：借款流程 + AI 风控

**目标**：用户能在 testnet 完整走完"看持仓 → AI 建议 → 签名 → 借 USDC"。

**任务**：
- [ ] [合约] `safeTransferFrom` + `onERC1155Received` 联调
- [ ] [合约] ERC-4337 UserOperation 示例脚本（approve + openLoan 合并）
- [ ] [后端] `POST /api/lending/risk-assessment`（AI 调用）
- [ ] [后端] `prompts/lending_risk.md` 第一版
- [ ] [后端] `POST /api/lending/prepare-borrow`（构造 UserOperation）
- [ ] [后端] `POST /api/lending/confirm-borrow`（等 tx + 落库）
- [ ] [后端] `EventIndexer` 初版：轮询 Vault 事件，5 秒一次
- [ ] [前端] `BorrowFlow`：AmountStep（AI 建议卡 + 滑块 + 联赛上限硬限）
- [ ] [前端] `BorrowFlow`：SigningStep（对接 Privy `sendTransaction`）
- [ ] [前端] `LendingDashboard` 骨架 + `LoanCard`
- [ ] [前端] 个人页 Products → Lending 入口改为跳新页
- [ ] [埋点] `lending.borrow.*` 事件 + 接 Segment

**Sprint 2 Done Gate**：
- Testnet 端到端：点 "Borrow" → AI 建议 → Privy 签名 → $250 USDC 到账 → Dashboard 显示仓位

### Sprint 3 · Week 5-6：还款 + Keeper + 预警

**目标**：关闭完整生命周期，Keeper 能自动清算。

**任务**：
- [ ] [后端] `POST /api/lending/prepare-repay`、`confirm-repay`
- [ ] [后端] `keeper_loop`：LTV + kickoff 双触发
- [ ] [后端] `alert_engine_loop`：70/80/kickoff-4h/kickoff-2h 幂等推送
- [ ] [后端] `price_watcher`（REST 轮询 10s）
- [ ] [合约] Mumbai 压测：模拟 100 笔并发清算
- [ ] [前端] `RepayModal`（全额 / 部分）
- [ ] [前端] `LtvBar`（健康/警告/危险三态）
- [ ] [前端] `LendingDashboard` 实时数据（每 30s 刷新）
- [ ] [前端] Service Worker：`chrome.notifications` 接预警
- [ ] [运维] Sentry 接入 Keeper 错误
- [ ] [运维] Grafana 初版看板（TVL / 活跃借款数 / 清算次数 / Keeper 延迟）

**Sprint 3 Done Gate（决策点 B）**：
- Keeper 模拟 100 场清算零失败
- 测试 TVL $100K 下模拟坏账率 < 2%
- 内部 3-5 人（团队）能用 Testnet 完整走通

### Sprint 4 · Week 7-8：审计 + 主网部署准备

**目标**：合约审计开始，主网部署前置全部就绪。

**任务**：
- [ ] [合约] **Week 7 前签约审计方**（阻塞项，必须 Sprint 1 就开始发 RFP）
- [ ] [合约] 审计返修 → 第 2 轮 review
- [ ] [合约] 主网部署脚本 + 2-of-3 multisig owner + 48h timelock
- [ ] [合约] Immunefi 漏洞赏金配置（$5K）
- [ ] [后端] 切主网 RPC（Alchemy prod key）
- [ ] [后端] Keeper 钱包：HSM / KMS 托管私钥（Google KMS or AWS KMS）
- [ ] [后端] PagerDuty 告警：Keeper 下线 / 连续 3 笔失败 / LP 池 < 50%
- [ ] [运维] 金丝雀环境：独立 backend instance + DB schema 但连主网
- [ ] [法务] Terms of Service v1.0 文案定稿
- [ ] [前端] i18n 补完 + QA

**Sprint 4 Done Gate**：
- 审计报告无 High/Critical issue
- 主网合约已部署，Treasury 注资 $10K（准备灰度）

### Sprint 5 · Week 9-10：Alpha 内测

**目标**：5 名内部 + 15 名外部种子用户，主网真金白银小额试用。

**任务**：
- [ ] [运维] 白名单机制：限定特定 wallet_address 才能开借款
- [ ] [产品] 单用户上限 $100，全局 TVL 上限 $5K
- [ ] [运维] 每日对账脚本：DB 记账 vs 合约状态（应完全一致）
- [ ] [运维] 每日 Keeper 健康报告（邮件）
- [ ] [支持] Discord 专用 channel + 反馈模板
- [ ] [数据] AI 建议 vs 实际清算数据收集
- [ ] Bug Bash：产品 + 工程全员每周一跑

**Sprint 5 Done Gate（决策点 C 前置）**：
- 50+ 笔真实借款完成
- 坏账率 < 3%
- 无 P0 事故
- NPS > 40

### Sprint 6 · Week 11-12：公测 + 拉新

**目标**：对所有已登录的 KPAX 用户开放，TVL 冲 $50K。

**任务**：
- [ ] [运维] 放开白名单
- [ ] [产品] 单用户上限提到 $500
- [ ] [产品] 全局 TVL 上限 $100K（触顶时 Treasury 补资）
- [ ] [营销] Chrome 通知：现有 Pro 用户定向推送
- [ ] [营销] X/Discord 发布
- [ ] [营销] 首笔借款免息 7 天活动（系统自动应用）
- [ ] [数据] KPI Dashboard 公开（TVL、借款次数、坏账率、AI 建议采纳率）
- [ ] [AI] 第二版 prompt（基于 Alpha 数据回训）
- [ ] [后端] `PriceWatcher` 升级到 CLOB WebSocket

**Sprint 6 Done Gate（决策点 C）**：
- TVL > $50K
- DAU 占插件 1%
- 坏账率 < 2%
- → 进入 Phase 2 筹备

---

## 10. 依赖与决策门

### 10.1 阻塞依赖

| 依赖 | Deadline | Owner | 状态 |
|------|----------|-------|------|
| 合约审计签约 | Week 3 结束 | Eng Lead | ⏳ Sprint 1 启动 |
| Polymarket 官方 CTF `safeTransferFrom` 测试 | Week 1 结束 | 合约工程师 | ⏳ |
| Privy ERC-4337 UserOperation 文档 | Week 2 结束 | 前端 | ⏳ |
| Treasury USDC $10K（Alpha） + $50K（Beta） | Week 8 / Week 10 | CFO | ⏳ |
| Keeper Gas 资金 $500/月 | Week 7 | Ops | ⏳ |
| HSM / KMS 选型 | Week 6 | Ops | ⏳ |
| Immunefi 赏金账户 | Week 7 | Eng Lead | ⏳ |

### 10.2 决策门（Go / No-Go）

**决策点 A · Week 2 结束**
- [ ] LTV/APR 参数经产品 + 工程 + 合规三方确认
- [ ] 合约架构 review 通过
- [ ] 审计方已签约 → 否则 Phase 1 时间线顺延

**决策点 B · Week 6 结束**
- [ ] Keeper 100 场模拟零失败
- [ ] 坏账模拟 < 2%
- [ ] 审计无 High/Critical → 否则延期进入 Alpha

**决策点 C · Week 10 结束**
- [ ] Alpha KPI 达标（50 笔 / 坏账 < 3% / NPS > 40）→ 否则延长 Alpha 2 周
- [ ] 决定是否按计划进公测

---

## 11. 测试策略

### 11.1 合约测试

| 层 | 工具 | 覆盖 |
|---|------|------|
| 单元 | Foundry `forge test` | 所有函数、所有 revert 路径 |
| 属性 | Foundry `forge fuzz` | 不变性断言 |
| Mainnet-fork 集成 | Foundry `--fork-url mainnet` | 真实 Polymarket Exchange 交互 |
| 链上 | Mumbai testnet 真实部署 | 端到端烟雾测试 |

关键不变性：
- `sum(active_loan.principal) + vault_usdc_balance == lpPoolBalance`
- `sum(active_loan.collateral_shares for same tokenId) <= vault_ctf_balance(tokenId)`
- `!active` 的 loan 永远不能再被 `liquidate`

### 11.2 后端测试

- `pytest` + `httpx.AsyncClient` — 覆盖所有 `/api/lending/*` 端点
- Mock `vault_client` 做 happy path，整合测试跑 Mumbai testnet
- **关键**：`current_debt` Python vs `debtOf` Solidity 跨语言对齐测试（可用 `brownie` 或 `pytest-foundry`）

### 11.3 插件测试

- 主要靠手动 QA + TestRail 用例
- 关键路径冒烟：ToS 签署、借款全流程、还款全流程、预警接收

### 11.4 Alpha 真实演练

- Week 5 Sprint 结束时，Eng + Product + PM 全员用 testnet 跑 10 笔借款
- Week 9 Alpha 启动后每周一次"模拟崩盘"演练：手动注入低价，确认清算触发

---

## 12. 监控与告警

### 12.1 指标

**业务指标（PRD §12.2）**：
- `kpax_lending.tvl_usd`
- `kpax_lending.active_loans`
- `kpax_lending.loans_opened_total`
- `kpax_lending.loans_liquidated_total`
- `kpax_lending.bad_debt_usd_total`

**系统指标**：
- `kpax_lending.keeper.scan_duration_ms`
- `kpax_lending.keeper.liquidation_latency_ms`（强平线触发 → tx 上链）
- `kpax_lending.event_indexer.lag_blocks`
- `kpax_lending.ai_assessment.latency_ms`
- `kpax_lending.ai_assessment.fallback_count`

### 12.2 告警（PagerDuty P1）

- Keeper 停摆 > 2 分钟
- EventIndexer 滞后 > 20 blocks
- 连续 3 笔 liquidate 交易失败
- LP 池 USDC < 总活跃借款 × 1.5
- Keeper 钱包 MATIC 余额 < 5
- 任一合约事件与 DB 状态不一致（每日对账脚本）

### 12.3 仪表盘

Grafana 两张板：
1. **Lending Biz**：TVL / DAU / 坏账率 / AI 采纳率
2. **Lending Ops**：Keeper 延迟 / Gas 消耗 / 告警触发 / API P95 延迟

---

## 13. 上线与回滚

### 13.1 灰度策略

| 阶段 | 受众 | 单用户上限 | 全局 TVL 上限 |
|------|------|-----------|--------------|
| Week 9 Alpha | 白名单 20 人 | $100 | $5K |
| Week 10 Alpha+ | 白名单 50 人 | $300 | $20K |
| Week 11 Beta | 所有已登录用户 | $500 | $50K |
| Week 12 GA | 所有用户 | $1,000 | $200K |

上限由后端配置，不需改合约。

### 13.2 紧急熔断

1. **暂停新借款**：`LendingVault.pause()`（onlyAdmin）
2. **暂停 Keeper**：运维切掉 `keeper_loop` 启动 flag，不影响合约正常运行
3. **全量紧急退出**：`admin.emergencyCloseAll()` 合约在本地回归测试中验证过 gas 不会爆

回滚流程文档 → `docs/runbooks/lending-incident-response.md`（Sprint 4 交付）。

---

## 14. 风险登记册

| # | 风险 | 等级 | 缓解 | Owner |
|---|------|------|------|-------|
| R1 | 合约审计延期 | 🔴 高 | Sprint 1 就发 RFP，锁定 Week 7-8 档期 | Eng Lead |
| R2 | Polymarket CTF 接口变更 | 🟡 中 | 建版本兼容层；监听 PM 合约升级事件 | 合约工程师 |
| R3 | Keeper 在牛市 gas 高峰被挤出 | 🟡 中 | EIP-1559 动态 tipCap + 多 RPC 备源 | 后端工程师 |
| R4 | AI 建议离谱（过高 LTV）导致坏账 | 🟡 中 | 合约硬上限限死联赛 Tier 最大 LTV | 产品 |
| R5 | 价格操纵（拉砸） | 🟡 中 | 5min TWAP + 异常熔断 | 合约工程师 |
| R6 | Treasury 资金不足 | 🟡 中 | 每周结算 + 预算 $200K buffer | CFO |
| R7 | 用户误操作（借完忘还 → 强平） | 🟢 低 | 多层预警 + 教育 | 产品 |
| R8 | 监管介入（美国 SEC） | 🟡 中 | ToS 明确"借贷工具非投资"，Privy 非托管 | 法务 |
| R9 | Keeper 私钥泄露 | 🔴 高 | HSM/KMS + 合约限定 Keeper 只能 liquidate | 运维 |
| R10 | 单 token 级联清算 | 🟡 中 | 单 token 借款 ≤ 20% 市场深度 | 产品 |

---

## 15. 后续阶段预留

### Phase 1.5（Week 13-16）改进项

- CLOB WebSocket 替代 REST 轮询
- 外部 LP 对接雏形（kpaxUSD LP token）
- 动态利率（Aave 式曲线）
- 借款历史页 + 后验分析展示
- AI 模型迭代：基于真实清算数据回训

### Phase 2（Week 17+）

- 开放外部 LP
- 多 token 组合抵押
- permissionless 清算（5% 清算者奖励）
- 非体育市场（选 Tier 1 热门加密、政治）
- 第二轮审计

---

## 附录

### A. 角色分工（建议）

| 角色 | 人数 | 职责 |
|------|------|------|
| 合约工程师 | 1 | Vault 合约 + 审计对接 |
| 后端工程师 | 1 | API + Keeper + Indexer |
| 前端工程师 | 1 | 插件 UI + Privy 集成 |
| AI/ML 工程师 | 0.5 | 风控 prompt + 模型迭代 |
| 运维 | 0.5 | Keeper 部署 + 监控 |
| 产品 | 0.5 | 需求把控 + Alpha 协调 |

### B. 预算（MVP 12 周）

- 审计费：$15K
- Immunefi 赏金池：$5K
- RPC / KMS / 监控：$500/月 × 3 = $1.5K
- Gas 费：$500/月 × 3 = $1.5K
- **Treasury 注资**：$50K（MVP LP 池）
- **总计**：$73K

### C. 参考文档

- PRD：`docs/kpax-lending-prd.md`
- 技术方案：`docs/tech-plan-centralized.md`
- 竞品分析：`docs/polymargin-research.md`
- 用户规则：`docs/kpax-lending-rules.md`

### D. 版本历史

- v0.1 (2026-04-23)：初版开发计划，对齐 PRD v0.1
