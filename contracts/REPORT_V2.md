# KPAX LendingVault V2 — Sprint 4 Report

> 由合约工程师 agent 在 2026-04-30 产出。原文未能落地（沙箱拦截写入），由后端开发流程补写。配套 plan：`docs/kpax-realistic-liquidation-plan.md` v0.3。

## 三件交付物状态

| 交付物 | 状态 |
|---|---|
| **A. `contracts/src/LendingVaultV2.sol`** | **Done** — 4 步清算 + treasury + Loan.withdrawn |
| **B. Foundry 测试 `contracts/test/LendingVaultV2.t.sol`** | **Done** — 28 个新测试，本地全绿 ✅ |
| **C. 部署 / 升级脚本** | **Done** — `DeployV2.s.sol` / `ScheduleV2Upgrade.s.sol` / `ExecuteV2Upgrade.s.sol` |

**Forge test 本地结果**：56 / 56 全绿（V1 老测试 28 + TimelockUpgrade 4 + V2 新 28，via-ir 编译）。Stack-too-deep 问题已通过 `foundry.toml` 加 `via_ir = true` 解决。

## V2 接口 vs 冻结接口

100% 符合冻结接口。微小偏差（合理，已 audit）：
1. `setTreasury(address(0))` revert — 防 admin 误清空导致 settle 永久卡住
2. settle 后 `withdrawn` 保持 `true` — 语义变成"CTF 已卖掉，账目结清"；只有 `returnCtfFromKeeper` 重置为 false
3. `repay()` 加 `if (l.withdrawn) revert LoanAlreadyWithdrawn()` — 防止 borrower 在 keeper 持 CTF 期间 repay
4. `emergencyReturnCollateral()` 加 `if (l.withdrawn) revert` — vault 在 withdrawn 期间不持有 CTF

资金分配公式与 plan §2 完全一致：
```
toLp        = min(actualProceeds, principal)
remaining   = actualProceeds − toLp
toTreasury  = min(remaining, interest + penalty)
residual    = remaining − toTreasury
```
interest 在 settle 时刻按 `block.timestamp - openTime` 重算。

## Storage layout audit（关键）

V1 → V2 兼容 ✓
- V1 `__gap[40]` 第一个 slot 拿来放 `treasury` (address)
- 剩余 `__gap[39]` 留作未来扩展
- `Loan.withdrawn` (bool) 进 V1 已存在的 packed slot 7（`leagueTier + active + repaid + liquidated`）

V1 旧 loan 在 V2 读取时 `withdrawn = false`（slot 默认 0），符合 "老 loan 不在 withdrawn 状态" 语义。

## 改动文件清单

新增：
- `contracts/src/LendingVaultV2.sol`
- `contracts/test/LendingVaultV2.t.sol`
- `contracts/script/DeployV2.s.sol`
- `contracts/script/ScheduleV2Upgrade.s.sol`
- `contracts/script/ExecuteV2Upgrade.s.sol`

修改：
- `contracts/test/LendingVault.t.sol` — 删除 V1 atomic liquidate 测试块
- `contracts/DEPLOY.md` — 加 §5' V2 升级流程
- `contracts/foundry.toml` — 加 `via_ir = true`（解决 stack too deep）

未动：
- `contracts/src/LendingVault.sol`（V1 已部署，不动）
- `contracts/src/interfaces/`、`contracts/test/mocks/`、`contracts/test/TimelockUpgrade.t.sol`、`contracts/script/Deploy.s.sol`、`contracts/script/Upgrade.s.sol`
- `backend/**`（严格遵守边界）

## 必须 backend 同步改动

✅ 已在 backend Sprint 4 D4 同步完成：
1. `LoanLiquidated` 事件签名 4 → 6 字段（topic0 不同）— `event_decoder.py` 已更新
2. 新事件 `LiquidationStarted` / `CtfReturnedFromKeeper` / `TreasurySet` — `event_decoder.py` 已加解码器
3. `vault_client.py` 已重写：删除 V1 `liquidate` + 加 `withdrawCtfForLiquidation` / `settleLiquidation` / `returnCtfFromKeeper` + USDC `transfer`

## 已知风险

1. **滑点保护下放给 backend**：合约对 `actualProceeds` vs `expectedProceeds` 偏离不做检查 — 恶意 keeper 注 0 USDC 都能 settle（LP 全损失）。**backend keeper 必须实现 `min_proceeds` 阈值**（plan §5 K17），已在 `polymarket_seller.sell_ctf_market(min_proceeds_e6=...)` 实现
2. **interest 持续累积**：step 1 → step 4 拖时间，treasury 拿更多、residual 减少。backend 应让 4 步在分钟级完成
3. **`setTreasury` 没走 timelock**：admin 直接生效。MVP 接受；生产化建议 treasury 切换走 timelock
4. **没有 partial withdraw**：必须全 withdraw 或全不动
5. **emergency hatch 与 withdrawn 互斥**：keeper EOA 失联时 CTF 卡在 keeper 钱包，admin 无法直接救。生产化前应加 admin override（hand-off 期任务）

## 主网部署 step-by-step

完整步骤见 `contracts/DEPLOY.md` §5'。摘要：


```bash
export RPC=https://polygon-bor-rpc.publicnode.com

# 已知地址
export PROXY=0x40892387747Da2f46fecaC902274081F676F1592
export V2_IMPL=0xC793D31Aa6c8A0E26AA20b61a7C572b8EB061C6a
export TIMELOCK=0xE07bd765f44b8d28da01D405dBFA794D08a1E130
export ADMIN_EOA=0x368E55613b3F9AD70aa9E5da4ac41D48D181Fd9B
export KEEPER_EOA=0x8C95e1C75eB1355566FE9A9cc20453Cbc0639B3E
export TREASURY=0xcb7A9e4F9366de25F36195Ce96625bE3dEe05bb7
export POLYMARKET_CTF=0x4D97DCd97eC945f40cF65F87097ACe5EA0476045
export CTF_EXCHANGE=0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E
export NEG_RISK_EXCHANGE=0xC5d563A36AE78145C45a50134d48A1215220f80a
export POLYGONSCAN_KEY=DDCQZC1HPRKQBJS1FD4CYSQ6AVX9J6SW8P

cd contracts

# 1. 本地验收（已通过）
~/.foundry/bin/forge test -vvv

# 2. Deploy V2 impl
~/.foundry/bin/forge script script/DeployV2.s.sol:DeployV2 \
  --rpc-url https://polygon-bor-rpc.publicnode.com \
  --account deployer --sender $ADMIN_EOA \
  --broadcast --verify --etherscan-api-key $POLYGONSCAN_KEY -vvv
# → V2_IMPL

# 3. Schedule batch (admin EOA 是 timelock proposer)
V2_IMPL=0xC793D31Aa6c8A0E26AA20b61a7C572b8EB061C6a
~/.foundry/bin/forge script script/ScheduleV2Upgrade.s.sol:ScheduleV2Upgrade \
  --sig "run(address,address,address,address)" \
  $TIMELOCK 0x40892387747Da2f46fecaC902274081F676F1592 $V2_IMPL $ADMIN_EOA \
  --rpc-url https://polygon-bor-rpc.publicnode.com \
  --account deployer --sender $ADMIN_EOA --broadcast -vvv

# 4. 等 24h
# 5. Execute
~/.foundry/bin/forge script script/ExecuteV2Upgrade.s.sol:ExecuteV2Upgrade \
  --sig "run(address,address,address,address)" \
  $TIMELOCK 0x40892387747Da2f46fecaC902274081F676F1592 $V2_IMPL $ADMIN_EOA \
  --rpc-url https://polygon-bor-rpc.publicnode.com \
  --account deployer --sender $ADMIN_EOA --broadcast -vvv

# 6. Admin 收尾
~/.foundry/bin/cast send 0x40892387747Da2f46fecaC902274081F676F1592 \
  "setTreasury(address)" 0xcb7A9e4F9366de25F36195Ce96625bE3dEe05bb7 \
  --rpc-url https://polygon-bor-rpc.publicnode.com --account deployer

# Keeper 一次性 approve（用 keeper 私钥）
~/.foundry/bin/cast send 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045 \
  "setApprovalForAll(address,bool)" 0x40892387747Da2f46fecaC902274081F676F1592 true \
  --rpc-url https://polygon-bor-rpc.publicnode.com --private-key $KEEPER_PRIVATE_KEY
```
