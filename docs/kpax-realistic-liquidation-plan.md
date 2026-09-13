# KPAX Lending — 真实清算闭环（Sprint 4）

> **状态**：Plan v0.3
> **日期**：2026-04-30
> **依赖**：Sprint 3（`docs/kpax-keeper-alert-plan.md` v0.2）已完成、UUPS proxy `0x40892387747Da2f46fecaC902274081F676F1592` 已上线
> **预计周期**：4–5 工作日 + 24h timelock = 5-6 日历日

---

## 0. 摘要

把 V1 "假定 USDC 已 pre-funded" 的 stub 路径补成**真实闭环**：CTF 真卖、USDC 真分发。

**新清算流程（4 步）**：

1. `keeper.withdrawCtfForLiquidation(loanId, priceE6)` — 链上：vault → keeper 转 CTF
2. keeper 链下：通过 Polymarket CLOB（py-clob-client）把 CTF 卖成 USDC
3. `keeper.usdc.transfer(vault, actualProceeds)` — 链上：USDC 注回 vault
4. `keeper.settleLiquidation(loanId, reason, actualProceeds)` — 链上：vault 分配资金

**资金分配**（V2 新逻辑）：

| 来源 | 流向 |
|---|---|
| principal（本金） | LP 池 |
| interest（利息） | **treasury（项目方）** |
| penalty（2%） | **treasury** |
| residual（残值） | borrower |

**升级路径**：UUPS upgrade，首次 schedule batch 同时做两件事：(a) `setUpgrader(adminEOA)` 关闭 timelock 治理 + (b) `upgradeToAndCall(V2_impl)`。等 24h execute → 之后 admin 直接 `upgradeTo()` 无 delay，开发顺畅。生产化时再 `setUpgrader(timelock)` 切回。

---

## 1. 范围

### 做
- LendingVaultV2 implementation + Foundry tests + 部署脚本（合约 agent）
- backend `polymarket_seller.py`（py-clob-client 集成）
- `vault_client.py` 加 V2 三个新接口
- `keeper.py` 改造成 4 步流程 + 错误回滚 + keeper USDC 监控
- 集成测试（mock Polymarket）+ 灰度小额演练

### 不做
- 主网 KMS / HSM（仍 .env）
- 多 keeper / leader election
- 真正的限价单策略（MVP 用 market sell，灰度小额）

---

## 2. 合约 V2 接口冻结

> **这是 backend 与合约 agent 的契约。任何一方变更都必须同步对方。**

### 新增

```solidity
// 资金 sink — admin 部署后立即调 setTreasury 设置
address public treasury;
function setTreasury(address newTreasury) external onlyAdmin;
event TreasurySet(address indexed previous, address indexed next);

// 4 步流程
function withdrawCtfForLiquidation(
    uint256 loanId,
    uint256 currentPriceE6
) external onlyKeeper returns (uint256 expectedProceeds);
// - revert: !active / withdrawn / liquidated / repaid / price 0 / price > 1e6
// - effect: l.withdrawn = true; ctf.safeTransferFrom(vault, msg.sender, …)
// - return: shares × priceE6 / 1e6（仅供 keeper 参考；settle 用 actualProceeds）
event LiquidationStarted(uint256 indexed loanId, uint256 currentPriceE6, uint256 expectedProceeds);

function settleLiquidation(
    uint256 loanId,
    string calldata reason,           // "ltv_breach" | "kickoff_due"
    uint256 actualProceeds
) external onlyKeeper;
// - revert: !withdrawn / liquidated / repaid / bad reason / vault.free < actualProceeds
// - effect: l.liquidated = true; l.active = false
// - distribute (saturating)：
//     debt = principal + interest
//     penalty = actualProceeds × 200 / 10_000
//     toLp = min(actualProceeds, principal)              -> lpPoolBalance
//     toTreasury = min(actualProceeds - toLp, interest + penalty)  -> treasury
//     residual = max(0, actualProceeds - toLp - toTreasury)        -> borrower EOA
event LoanLiquidated(
    uint256 indexed loanId,
    string reason,
    uint256 actualProceeds,
    uint256 toLp,
    uint256 toTreasury,
    uint256 residualToBorrower
);

// 错误恢复 — keeper 卖 CTF 失败时调
function returnCtfFromKeeper(uint256 loanId) external onlyKeeper;
// - revert: !withdrawn / liquidated
// - effect: l.withdrawn = false;
//           ctf.safeTransferFrom(msg.sender, vault, …)（要求 keeper 已 setApprovalForAll(vault, true)）
event CtfReturnedFromKeeper(uint256 indexed loanId);
```

### 修改 / 删除

- **删除** V1 `liquidate(uint256, string, uint256)` — 4 步流程取代
- `LoanLiquidated` 事件签名改了（多 toLp / toTreasury / residualToBorrower 三个字段）— **后端 event_decoder 必须同步**
- `Loan` struct 加 `bool withdrawn`（防重入 + reaper 用）

### Storage 兼容性

V1 已有 `__gap[40]` 预留 — 新加 `treasury` (1 slot) + `Loan.withdrawn`（loan 字段不影响 layout 因为 mapping 内部）。安全。

---

## 3. 任务拆解

### Day 1 · 合约 V2（合约 agent，并行）

- [ ] LendingVaultV2.sol：按 §2 冻结接口实现
- [ ] Foundry tests：4 步流程 happy path + 边界（returnCtf / 滑点 / 各资金分配分支）
- [ ] V2 部署脚本：`script/DeployV2.s.sol`（仅部署 implementation 地址）
- [ ] V2 升级脚本：`script/ScheduleV2Upgrade.s.sol`（schedule timelock batch：setUpgrader + upgradeToAndCall）

### Day 2 · V2 部署 + schedule timelock batch

- [ ] forge script 部署 V2 implementation 到 Polygon → 拿到 V2_impl 地址
- [ ] forge script schedule batch（admin EOA 通过 timelock proposer 角色）：
  - call 1：`vault.setUpgrader(0x368E55...)`
  - call 2：`vault.upgradeToAndCall(V2_impl, 0x)`
- [ ] 记录 schedule operationId，24h 后 execute 用

### Day 2-3 · 24h timelock 等待（不阻塞 backend 并行）

### Day 3 · timelock execute + 验证

- [ ] 24h 到，admin 调 `timelock.execute(...)` → V2 上线 + admin 接管 upgrader
- [ ] admin 调 `vault.setTreasury(0xcb7A9e4F9366de25F36195Ce96625bE3dEe05bb7)`
- [ ] 链上验证：`vault.upgrader() == admin`、`vault.treasury() == 0xcb7A...`、`vault.usdc()` 等 storage 完整
- [ ] keeper EOA 调 `ctf.setApprovalForAll(vault, true)`（一次性，returnCtfFromKeeper 路径需要）

### Day 4 · backend 接口层

- [ ] **polymarket_seller.py**（新建）
  - 依赖：`py-clob-client`（加 requirements.txt）
  - 接口：`async sell_ctf_market(token_id: str, shares: int, slippage_bps: int = 200) -> SellResult`
  - 行为：market sell（IOC 或 fill-or-kill）；`SellResult{success, actual_proceeds_usdc_e6, fill_tx_hash, error}`
  - 单一职责：**只**封装 Polymarket CLOB 卖单；不知道 vault / loan
- [ ] **vault_client.py** 加三个新接口：
  - `withdraw_ctf_for_liquidation(loan_id, price_e6) -> tx_hash`
  - `settle_liquidation(loan_id, reason, actual_proceeds) -> tx_hash`
  - `return_ctf_from_keeper(loan_id) -> tx_hash`
- [ ] 删除 `vault_client.liquidate()` 旧接口（V1 原子版本）+ 迁移调用方

### Day 5 · keeper.py 改造

- [ ] `_trigger_liquidation` 改成 4 步状态机：
  ```
  active → withdrawing (DB 新增过渡态) → liquidating → liquidated_*
  ```
- [ ] 错误回滚：每一步失败的处理路径
  - withdraw 失败：状态不动，next tick 重试
  - sell 失败：调 `returnCtfFromKeeper`，DB withdrawing → active
  - transfer USDC 失败：状态保持 withdrawing，告警 admin
  - settle 失败：USDC 已注 vault，告警 admin（emergencyWithdrawERC20 可恢复）
- [ ] `Loan.LOAN_STATUSES` 加 `withdrawing`
- [ ] alembic migration 加 DB 状态相关字段（`withdrawn_at: datetime | None`，给 reaper 用）
- [ ] keeper EOA 监控 tick：每 tick 查 USDC + MATIC 余额，低于阈值 Sentry 告警

### Day 6 · 测试 + 灰度演练

- [ ] 单元测试：4 步流程每步独立 + 错误回滚分支
- [ ] e2e 集成测试：FakePolymarketSeller + FakeVaultClient 走完整闭环
- [ ] **主网灰度小额演练**（首次真实清算）：
  - admin 借 $1 loan
  - 改 DB `match_kickoff_at` = `now + 1.5h` 触发 keeper
  - 看：CTF 真离开 vault → Polymarket 真 fill → USDC 真到 keeper → vault 收 USDC → treasury 收利息+罚金 → borrower 收残值

---

## 4. 关键决策

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| K13 | penalty 归属 | treasury（不归 LP） | 项目方收入语义一致（与 interest 同组） |
| K14 | treasury 地址 | `0xcb7A9e4F9366de25F36195Ce96625bE3dEe05bb7` | 用户指定 |
| K15 | 清算 atomic vs 4 步 | 4 步（withdraw → sell → transfer → settle） | atomic 要合约接 Polymarket Exchange，复杂；4 步链下卖更灵活 |
| K16 | CTF 卖不掉的恢复 | `returnCtfFromKeeper` + DB 回 active | 保留下次 tick 重试通道 |
| K17 | 滑点处理 | 用 actualProceeds（saturating） | 沿用 V1 under-recovery 逻辑，LP 风险已知 |
| K18 | 开发期 timelock | 首次 schedule batch 把 upgrader 切给 admin | 之后 admin 直接 upgradeTo 无 delay；生产化再 setUpgrader 切回 |
| K19 | Polymarket 集成方式 | `py-clob-client`（官方 SDK） | 比手撸 EIP-712 + REST 稳定 |
| K20 | 旧 atomic liquidate | 删除（V2 不保留） | 干净，避免双路径 |
| K21 | DB 新过渡态 | `withdrawing`（active → withdrawing → liquidating → liquidated_*）| 区分 "CTF 已转出但未 settle" |

---

## 5. 失败模式 & 兜底

| 故障 | 影响 | 兜底 |
|---|---|---|
| keeper 链下卖 CTF 失败（流动性 / 滑点 / 网络） | CTF 卡 keeper EOA | `returnCtfFromKeeper` 把 CTF 还回 vault，loan 回 active |
| keeper 调 `usdc.transfer` 失败（gas 不足等） | CTF 在 keeper、vault 没 USDC | 状态保持 withdrawing，admin 告警 → 手动注 USDC + settle 或 returnCtf |
| `settleLiquidation` revert | USDC 已注但状态 withdrawing | admin 用 emergencyWithdrawERC20（合约已有）取回 USDC，returnCtf 重置 |
| Polymarket CLOB API 宕机 | 无法卖 CTF | keeper 跳过这个 loan，下次 tick 重试 |
| keeper EOA USDC / MATIC 不足 | 无法 transfer / 发交易 | tick 监控 + Sentry 告警；admin 充值 |
| keeper 卖出价远低于 estimatedProceeds | LP 吃 under-recovery 损失 | 合约 saturating 已处理；keeper 端可加 `min_proceeds = expected × 0.9` 拒收滑点过大的 fill |
| treasury 地址写错 | 利息 / 罚金跑去错误地址 | admin `setTreasury` 可改（但已发出去的钱回不来） |

---

## 6. Done Gate（Sprint 4 验收）

- [ ] V2 implementation 部署 + timelock batch executed
- [ ] `vault.upgrader() == admin EOA`、`vault.treasury() == 0xcb7A...`
- [ ] keeper EOA `ctf.isApprovedForAll(keeper, vault) == true`
- [ ] Foundry V2 tests 全绿（含 4 步 happy + returnCtf + 资金分配各分支）
- [ ] backend 单元测试：keeper.py 4 步流程 + 错误回滚每条路径
- [ ] e2e 集成测试通过（mock Polymarket）
- [ ] 主网灰度真实清算 1 笔成功（admin 借 $1 → kickoff_due 触发 → 全链路走通）

---

## 7. 后续

- 限价单策略（PVP / TWAP）— 大额清算用
- Treasury 多签（项目方收入钱包应该是 Safe，不是 EOA）
- 监控 dashboard（Grafana）
- KMS / HSM（admin / keeper 私钥）
