# KPAX Vault 迁移计划 → pUSD（Polymarket V2 collateral）

**状态：** 草案，等审阅
**作者：** keeper engineering
**起因：** Polymarket CLOB V2 在 2026-04-28 升级后，把 USDC.e 替换成了 pUSD 作为唯一可用 collateral；KPAX vault 还在用 Native USDC 结算，所以 keeper 清算流程里 proxy 卖单后拿到的 pUSD 无法直接打回 vault。

---

## 1. 目标

让自动清算在 PM CLOB V2 上跑通端到端，同时**不动 LP 那边的接口**。

- LP 接触面：**不变**（充值 / 提现都是 Native USDC）
- 借款人接触面：借出来的 pUSD 直接落到他在 PM 的 proxy 里，立刻可在 PM 上交易
- keeper 接触面：5 步清算改成结算 pUSD，vault 内部再 unwrap 回 USDC

唯一的新依赖是 PM 的 `CollateralOnramp.wrap()` 和 `CollateralOfframp.unwrap()`，两者都是 1:1 的 USDC↔pUSD 换币（PM 文档保证），不会引入经济损益，只是多一点 gas。

## 2. 架构（迁移后）

```
                                              [PM CLOB V2]
LP                                              ↑
  │                                             │ pUSD
  │ USDC                                  CTF ↓ ↑ pUSD
  ▼                                             │
┌─────────────────────────┐                  ┌──────────────────────┐
│   LendingVaultV3 impl   │                  │  借款人的 PM proxy   │
│ （proxy 地址不变）      │                  │  (DepositWallet V2)  │
│                         │  Onramp.wrap     │                      │
│ ─ USDC reserves ────────┼──────────────▶   │ pUSD 落到这里        │
│   （LP 充值进来的）     │                  └──────────────────────┘
│                         │
│ ─ openLoan ────────────▶│  把 USDC wrap 成 pUSD，发到 borrower proxy
│ ─ repay ───────────────▶│  从 proxy 拉 pUSD → unwrap → 回到 USDC reserves
│ ─ settleLiquidation ───▶│  从 keeper proxy 拉 pUSD → unwrap → 按规则分配 USDC
│                         │
└─────────────────────────┘                  ┌──────────────────────┐
                                              │   keeper 的 PM proxy │
                          Offramp.unwrap      │   (DepositWallet V2) │
                          ◀──────────────────│   PM CLOB 卖单的 pUSD │
                                              │   ↑ 收益              │
                                              └──────────────────────┘
```

LP 永远只看到 USDC，wrap/unwrap 全在 vault 内部完成。

## 3. Phase 1 — 预先验证（**阻塞性**：通不过就停手）

合约一行不写之前，先用三个链上实验确认 wrap/unwrap 这条路径对我们的 token 和签名者真的能跑通。任意一项失败就喊停，重新评估。

### 3.1 Onramp 接受哪些 input asset

**问题：** `CollateralOnramp.wrap(USDC, ...)` 是只收 USDC.e（`0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`），还是 Native USDC（`0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359`）也收？

**测试：**
```python
# scripts/verify_pusd_pre_flight.py — STEP 3.1
# 只读探测：从 Onramp bytecode / ABI 里查支持的 asset 列表
# 或者直接发一笔 0 wei 的 staticcall 看 revert 信息
```

如果 Onramp 只认 USDC.e，那 vault 要么先把 USDC swap 成 USDC.e，要么 LP 改充 USDC.e。这两种都是较大的产品决策，需要单独定。

**通过条件：** Onramp 接受 Native USDC，**或** 我们达成共识 LP 改用 USDC.e。

### 3.2 真金白银 round-trip 测试（用 keeper 的 $1）

**问题：** keeper proxy 里现有的 1.000397 pUSD 能不能通过 PM Relayer 走 Offramp 真换回 USDC？

**测试：**
```python
# scripts/verify_pusd_pre_flight.py — STEP 3.2
# 通过 PM Relayer 提交一个 2-call 的 Batch:
#   1. pUSD.approve(Offramp, 0.5e6)
#   2. Offramp.unwrap(USDC, keeper_eoa, 0.5e6)
# 签名 → /submit → 轮询。验证 keeper EOA 的 USDC 余额涨了 0.5。
```

**通过条件：** Batch 状态走到 STATE_MINED，keeper EOA 收到 0.5 Native USDC（或 0.5 USDC.e，如果 3.1 决定是这条路）。

这一步同时验证了：PM Relayer 鉴权 + EIP-712 签名 + Batch 提交 + 状态轮询的整条链路。**通过这一步等于把 keeper step 4 的所有不确定性都消除了**。

### 3.3 Wrap 测试（USDC → pUSD）

**问题：** 任意 EOA 把 USDC 送进 Onramp.wrap 能拿回 pUSD 吗？

**测试：** keeper EOA（在 3.2 之后已经有 USDC 了）：
```python
# scripts/verify_pusd_pre_flight.py — STEP 3.3
# 直接 EOA 调用（wrap 不需要 relayer，谁都能调）：
#   1. usdc.approve(Onramp, 0.3e6)
#   2. Onramp.wrap(USDC, keeper_proxy, 0.3e6)
# 验证 keeper proxy 的 pUSD 涨了 0.3。
```

**通过条件：** Native USDC 离开 EOA，pUSD 出现在 proxy 里。

这条路径就是后面 vault `openLoan` 要走的——必须确认任意 EOA（具体来说：**vault 合约自己**）能调这个方法。

### 3.4 Relayer API key 是否能跨 signer 转发

**问题：** PM 给你的 Relayer API key 绑定的是 keeper EOA `0x8C95…9B3E`。能不能用这把 key 提交一个**不同 EOA 签名的 Batch**？如果可以，KPAX 一把 key 就能为所有借款人 repay 转发，不用每用户各自创建 key。

**测试：**
```python
# scripts/verify_pusd_pre_flight.py — STEP 3.4
# 用 KPAX 的 RELAYER_API_KEY，但 from / signature 用一个临时生成的 EOA。
# Batch 内容：一笔 0 wei dust call (e.g. pUSD.transfer(self, 0))
# 看 PM 是返回 200 OK，还是 401/403 拒绝。
```

**通过条件：**
- 200 OK + Batch 落到 STATE_MINED → KPAX 可用一把 key 服务所有借款人，repay onboarding 零摩擦
- 401/403 → 每个借款人必须各自创建 Relayer key，加进 KPAX 扩展的 onboarding 流程

---

## 4. Phase 2 — 合约改动

### 4.1 新文件 `contracts/src/LendingVaultV3.sol`

继承 `LendingVaultV2`，覆盖 3 个方法 + 加新的 storage：

```solidity
contract LendingVaultV3 is LendingVaultV2 {
    /// @custom:storage-location erc7201:kpax.lending.vault.v3
    struct V3Storage {
        address pUSD;
        address onramp;
        address offramp;
    }

    function _v3() internal pure returns (V3Storage storage $) {
        // ERC-7201 派生 slot — 跟 V2 隔离
    }

    /// @notice 升级后一次性写入 pUSD/onramp/offramp + 一次性 max-approve
    function initializeV3(address _pUSD, address _onramp, address _offramp) external onlyOwner {
        // ... + 给 onramp/offramp 一次性 max approve
    }

    /// @notice 覆盖：wrap USDC → pUSD，发到 borrowerProxy
    function openLoan(
        address collateralSource,
        uint256 ctfTokenId,
        uint256 shares,
        uint256 principal,
        uint256 matchKickoff,
        uint8 leagueTier,
        address borrowerProxy   // 新增参数
    ) external returns (uint256 loanId) {
        // ... 原有 collateral 拉取流程不变 ...
        // 把原本的 `usdc.transfer(msg.sender, principal)` 替换为：
        Onramp(_v3().onramp).wrap(usdc, borrowerProxy, principal);
        // ... 之后流程不变
    }

    /// @notice 覆盖：pUSD 进来，vault 内部 unwrap 成 USDC
    function repay(uint256 loanId) external {
        // 借款人的 PM proxy 是 msg.sender（通过 Relayer 转发触发）
        IERC20(pUSD).transferFrom(msg.sender, address(this), totalDebt);
        Offramp(_v3().offramp).unwrap(usdc, address(this), totalDebt);
        // ... 原有结算逻辑不变（仍以 USDC 计价）
    }

    /// @notice 覆盖：keeper proxy 送来 pUSD；unwrap → 按规则分配 USDC
    function settleLiquidation(uint256 loanId, string calldata reason, uint256 actualProceeds) external {
        IERC20(pUSD).transferFrom(msg.sender, address(this), actualProceeds);
        Offramp(_v3().offramp).unwrap(usdc, address(this), actualProceeds);
        // ... 原有分账逻辑不变（LP/treasury/borrower 都以 USDC 拿钱）
    }
}
```

**实现时要回答的开放问题：**
- Onramp.wrap 的 `_to` 是不是任意地址都行（我们希望 pUSD 直接落到 borrowerProxy，不要中转）。3.3 验证。
- `repay` 的 caller 到底是借款人 EOA 还是 proxy？如果是 proxy，msg.sender = proxy，照写就行；如果是 EOA，EOA 那边没 pUSD（pUSD 在 proxy 里），需要单独的 approval 通道。**结论：repay 必须从 proxy 通过 PM Relayer 触发**。

### 4.2 Storage layout 安全

用 ERC-7201 namespaced storage，V2 已有 slot 不动，V3 只在新 slot 写 3 个地址。

编译完做一次 layout diff 防呆：
```
forge inspect LendingVaultV3 storage-layout > /tmp/v3-layout.json
forge inspect LendingVaultV2 storage-layout > /tmp/v2-layout.json
diff /tmp/v2-layout.json /tmp/v3-layout.json   # 应该只有 additions
```

### 4.3 测试 (`contracts/test/LendingVaultV3.t.sol`)

- `test_OpenLoan_PUSDLandsInBorrowerProxy`
- `test_Repay_PUSDFromProxyUnwrappedToVault`
- `test_SettleLiquidation_PUSDFromKeeperUnwrappedToVault`
- `test_LPFlow_DepositWithdrawUSDC_NoChange` ← 回归测试
- Storage layout 测试（预先填 V2 数据 → 升级到 V3 → 验证 V2 字段还在）

---

## 5. Phase 3 — 后端 / keeper 改动

### 5.1 `prepare-borrow` 接口（`backend/app/routers/lending.py`）

`PrepareBorrowResponse` 加 `borrowerProxy` 字段。后端从借款人 EOA 通过 PM 的 V2 factory 派生：
```python
# proxy_resolver.py 加新工具函数：
async def derive_v2_deposit_wallet(eoa: str, rpc_url: str) -> str:
    # 调 factory.predictWalletAddress(impl, bytes32(eoa))
```

如果借款人的 V2 wallet 还没部署，前端要给提示（"先在 Polymarket V2 充 $1 激活账户"）。

### 5.2 `repay` 流程（`backend/app/routers/lending.py`）

借款人的 repay 必须从他的 PM proxy 触发（msg.sender 必须 = proxy）。两种方案：
- **a)** 扩展里让借款人签 EIP-712 Batch，后端用 **借款人自己的** Relayer API key 提交。每个用户都要有自己的 key。
- **b)** 让用户在 PM Web UI 里手动批准（弱集成）。

**推荐 a。** 借款人必须先创建一把 PM Relayer API key，这个加进 onboarding 流程。

### 5.3 keeper.py — step 4 从 USDC 改成 pUSD

`pm_relayer.py` 已经就位。Phase 2 升级完之后：
- step 4: `relayer.transfer_erc20(pUSD, vault, actualProceeds_e6)`
- vault 内部调 `offramp.unwrap` 把 pUSD 换成 USDC
- step 5: `vault.settleLiquidation(...)` 不变

回滚路径（卖单失败）：
- `relayer.transfer_erc1155(CTF, keeper_eoa, tokenId, shares_e6)` — proxy → EOA CTF
- `vault.returnCtfFromKeeper(loanId)` — vault 拉回

---

## 6. Phase 4 — 部署 & 迁移

### 6.1 Dev 环境（不上 timelock）

用户明确要求 dev 环境跳过 timelock。两个子方案：

- **方案 a：** 把 timelock 的 `minDelay` 改成 0（timelock 自己 schedule 一次 + 立即 execute 一次 `updateDelay(0)`）。之后 `Upgrade.s.sol` 原封不动用，delay = 0 即时升级。**修改可逆**：上 prod 时再把 delay 改回去。
- **方案 b：** 完全绕开 timelock，把 vault 的 `_authorizeUpgrade` 直接换成 admin EOA。**风险大**——prod 必须保留 timelock，所以转过去再转回来的迁移成本不小。

**推荐 a。** 改 delay 是单次操作、可逆；改授权路径是双向迁移，dev/prod 切换更折腾。

### 6.2 部署步骤

```
1. forge build
2. forge script script/DeployV3Impl.s.sol --broadcast    # 部署 LendingVaultV3 impl
3. （一次性）把 timelock minDelay 改成 0
4. forge script script/Upgrade.s.sol:UpgradeSchedule \
     <timelock> <proxy> <V3 impl>                        # schedule（delay=0）
5. forge script script/Upgrade.s.sol:UpgradeExecute \
     <timelock> <proxy> <V3 impl>                        # 立即 execute
6. cast send <proxy> "initializeV3(address,address,address)" \
     <pUSD> <onramp> <offramp>                           # 一次性接线
7. 后端部署（新 prepare-borrow / keeper 代码）
8. 扩展构建 + 推送（前端显示 + repay 走 relayer 流程）
```

### 6.3 迁移现存 loan #53

`0xAA35…8Ab1` 这位借款人，在 V2 切换之前借的，链上记账还是按 USDC.e 路径。升级到 V3 之后这条 loan 还指向旧的 USDC。三个选项：

- **a)** V3.repay/settle 里 special-case：`loan.openedBefore < migrationTimestamp` 就走旧的 V2 USDC 路径
- **b)** 升级前先把 #53 用 admin 通道手动清算（前面"路线 C"的方式：PM UI 卖单 + admin 救援 + settle），从干净状态进 V3
- **c)** 升级后 admin function 直接改链上 storage 强制收掉

**推荐 b。** 合约改动最小。

---

## 7. 风险登记表

| 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|
| 3.1 不通过（Onramp 不收 Native USDC）| 中 | 大 | LP 改用 USDC.e，或 vault 内嵌 USDC↔USDC.e swap |
| 3.2 不通过（Relayer 不能从 proxy unwrap）| 低 | 致命 | 立刻停止迁移；V2 keeper 没有别的路 |
| 升级后 storage 错位 | 低 | 灾难 | ERC-7201 namespace + storage-layout diff CI 检查 |
| 借款人没 V2 PM proxy | 中 | 中 | 扩展提示"激活 PM V2 账户" |
| 借款人没 Relayer API key（repay 用）| 高 | 中 | onboarding 增加创建 key 步骤；备选回到 PM UI |
| pUSD 脱锚 | 极低 | 灾难 | 超出本计划范围——属于 PM 系统性风险 |

---

## 8. 工作量估算

| 阶段 | 工作内容 | 实际时长 |
|---|---|---|
| 3 | 预先验证脚本 + 真实跑一遍 | 1–1.5h |
| 4 | LendingVaultV3 + 测试 | 2–3h |
| 5 | 后端 / keeper / 扩展接线 | 2h |
| 6 | 部署 + 迁移 loan #53 + 验证 | 1h |
| **合计** | | **6–7.5h** |

---

## 9. 决策记录（已敲定）

| # | 问题 | 决策 |
|---|---|---|
| 1 | Onramp 不收 Native USDC 的 fallback | **改 LP 充 USDC.e**（vault underlying 切换到 `0x2791…4174`），不在 vault 内嵌 swap |
| 2 | 借款人 repay 是否需要自己的 Relayer key | **每用户必须自己创建一把 PM Relayer key**（已被 Phase 1 §3.4 链上验证：API key 紧绑 1 个 EOA，KPAX 一把 key 不能为其他 signer 转发）。借款人 onboarding 加一步"在 PM 后台创建 Relayer API key 并粘到 KPAX 扩展"。 |
| 3 | loan #53 怎么处理 | **升级前用 admin 通道手动清算**（PM UI 卖单 + admin settle），从干净状态进 V3 |
| 4 | dev 怎么跳过 timelock | **把 timelock minDelay 设成 0**（schedule + 立刻 execute），不动 vault 的 `_authorizeUpgrade` 路径 |

---

## 9.1 §1 决策的连带影响

LP 改充 USDC.e 意味着：
- vault.usdc() 从 Native USDC（`0x3c499…3359`）切换到 USDC.e（`0x2791…4174`）
- LP UI / 前端 deposit 表单需要切换 token 地址 + 加引导（"如果你只有 Native USDC，先在 Uniswap 1:1 swap 成 USDC.e"）
- Native USDC 上 vault 现存的余额需要在升级前清空（或迁移到 USDC.e）
- **`initializeV3` 同时把 storage 里的 underlying 从 Native USDC 改成 USDC.e**

具体实施时这个迁移要细化（vault 余额迁移 + LP token 重计价 + 通知现有 LP），写进 Phase 4 部署 step list。

---

## 10. 审批门

审阅人对 §3 验证范围 sign-off **后** 才进 Phase 2。如果验证发现阻塞性问题，本计划要修订；不在假设上写合约代码。

---

## 11. Phase 1 验证执行结果（2026-05-05）

| Step | 结果 | 关键证据 |
|---|---|---|
| 3.1 | ✅ Onramp 只接受 USDC.e | Native USDC → `OnlyUnpaused()`；USDC.e → `TransferFromFailed()`（探测预期 revert）|
| 3.2 | ✅ pUSD→USDC.e Relayer 端到端 | tx `0xa4c1fc5e85427f3f331654ed986fe6c89fcf725beb693d9696a10dc86037b3ed`，proxy −0.5 pUSD / keeper EOA +0.5 USDC.e，1:1 精确匹配 |
| 3.3 | ✅ Onramp.wrap 1:1 | 直接 EOA 调用，keeper EOA −0.3 USDC.e / proxy +0.3 pUSD |
| 3.4 | ⚠️ key 紧绑 1 个 EOA | 控制组返回 400 `wallet registry validation failed`，实验组（更换 RELAYER_API_KEY_ADDRESS 为随机地址）返回 401 `invalid authorization` |

**结论：Phase 1 全部通过，可以进 Phase 2。**

Phase 1 衍生的 plan 修订：
- §9 决策 #2 锁定为"每用户一把 Relayer key"
- §5.1 borrower onboarding 增加"创建 PM Relayer key"步骤（前端引导 + KPAX 后端存用户的 key）
- 所有 Phase 1 钱在 keeper proxy 里：跑完后 0.800397 pUSD 在 proxy + 0.2 USDC.e 在 keeper EOA（本来 1.000397 pUSD，0.5 unwrap → 0.3 wrap 回，余 0.2 USDC.e 在 keeper EOA 备用）。Phase 2 / 3 / 4 测试可以接着用，不用再充。
