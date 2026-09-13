# KPAX LendingVault — Sprint 4 Report

> 由合约工程师 agent 在 2026-04-29 产出。原文未能落地（沙箱拦截），由后端开发流程补写。配套 plan：`docs/kpax-keeper-alert-plan.md` v0.2。

## 三件交付物状态

| 交付物 | 状态 | 备注 |
|---|---|---|
| **A. `liquidate` 真实实现 + 价格参数** | Done | 接口与冻结签名 100% 一致 |
| **B. UUPS Proxy 化 + 24h Timelock** | Done | OZ 5.x，Timelock self-administered |
| **C. Foundry 测试 + 部署脚本** | Partial（代码 done，编译/测试本机未跑） | agent 沙箱拒绝执行 `forge`，需要本地 `forge test` 验证 |

**唯一阻塞项**：agent 会话内 `forge` 二进制被沙箱拒绝。所有代码已完整实现并按公式手算交叉验证；强烈建议 pull 后立刻 `forge build && forge test -vvv`。

## 改动清单（所有文件均在 `contracts/`）

- `src/LendingVault.sol`：全文重写为 UUPS-upgradeable + 真实 liquidate
- `script/Deploy.s.sol`：重写为 implementation → timelock → proxy 一条龙
- `script/Upgrade.s.sol`：新增，schedule + execute 两个 forge script
- `test/LendingVault.t.sol`：全部测试改为基于 ERC1967Proxy；新增 12 个 liquidate 测试 + 3 个 UUPS upgrade 测试
- `test/TimelockUpgrade.t.sol`：新增，4 个 timelock-gated upgrade 集成测试
- `test/mocks/LendingVaultV2Mock.sol`：新增，验证 upgrade 后状态保留
- `test/mocks/NonUUPSImplMock.sol`：新增，验证 UUPS 拒绝非 UUPS 实现
- `DEPLOY.md`：重写，覆盖 proxy 部署 / 升级流程 / multisig 切换 / 旧 vault wind-down

## A. liquidate 行为细节

- 接口与冻结签名**完全一致**（`function liquidate(uint256, string calldata, uint256) external onlyKeeper`，`event LoanLiquidated(uint256 indexed, string, uint256, uint256)`）。多了 `nonReentrant` 修饰，不影响 ABI。
- reason 用 `keccak256(bytes(reason)) == REASON_*` O(1) 比较；`InvalidReason()` 拒绝其他字符串
- price sanity：`> 0 && ≤ 1_000_000`，否则 `InvalidPrice()`
- proceeds 公式：`shares * priceE6 / 1e6`
- penalty：2% on proceeds
- 三档分支：proceeds ≥ debt+penalty → 残值给 borrower；otherwise → 全归 LP，no residual（含 under-recovery，LP 吃损失）
- 入口前检查 `vault.balanceOf(USDC) - lpPoolBalance ≥ proceeds`，否则 `InsufficientProceeds()`
- 已 repaid / 已 liquidated 通过新加的 `Loan.repaid` / `Loan.liquidated` flags 拒绝（旧测试 `_, _, _, _, _, _, _, _, active` destructure 会断，已全部更新为 11 字段）
- CEI 顺序：先 mark loan 状态 → 后转账

CTF 卖出走"最简" MVP 路线：keeper 链下卖、把 USDC `transfer` 进 vault、再调 `liquidate`。合约不接 Polymarket Exchange。

## B. UUPS 架构

- `Initializable` + `UUPSUpgradeable` 来自 OZ 5.x 标准包（`lib/openzeppelin-contracts/`），**没有引入 `@openzeppelin/contracts-upgradeable`**（5.x 之后两边代码已同步，功能等价；详见风险 #1）
- `constructor() { _disableInitializers(); }` 锁实现
- `initialize(usdc, ctf, exchange, admin, keeper, upgrader)`，初始化校验所有非空（exchange 允许 0）
- 角色：`admin` (普通管理) / `keeper` (only `liquidate`) / `upgrader` (only `_authorizeUpgrade`)，全是 single address，简单可读
- `__gap[40]` 预留升级空间
- 生产 `upgrader = TimelockController(delay=24h, proposer/executor=multisig, admin=0)`
- ReentrancyGuard 用 OZ 标准版（非 upgradeable），proxy 模式下首次调用 `_status` 为 0 但与 `NOT_ENTERED(1)` 行为等价（仅多一次 SSTORE）

## C. 测试矩阵（共 30+ test cases）

`test/LendingVault.t.sol`：
- 初始化：2 个（second-call、impl-direct-call）
- openLoan / repay / proxy ownership：10 个（保留 Sprint 2 全部）
- admin gating：4 个
- **liquidate（新增 12 个）**：happy ltv_breach / happy kickoff_due+expectEmit / non-keeper / zero-price / >1e6 price / =1e6 边界 / invalid reason / repaid loan / already liquidated / insufficient proceeds / under-recovery LP loss / mid-band proceeds (debt < proceeds < debt+penalty)
- emergency hatches：5 个（保留）
- **UUPS upgrade（新增 3 个）**：authorized success + state survives / unauthorized rejected / non-UUPS impl rejected

`test/TimelockUpgrade.t.sol`（新增）：
- 4 个：immediate execute blocked / execute after 24h+1 success / non-proposer schedule rejected / sub-min-delay schedule rejected

跑法：
```bash
cd contracts
forge build
forge test -vvv
```

## D. 部署脚本

`script/Deploy.s.sol` 现在按顺序部署 implementation → TimelockController(24h, [multisig], [multisig], 0) → ERC1967Proxy(impl, abi.encodeCall(initialize, ...))。控制台输出"USE THIS in backend/.env: KPAX_VAULT_ADDRESS = <proxy>"。

`script/Upgrade.s.sol`：`UpgradeSchedule` + `UpgradeExecute` 两个合约，配合 timelock 24h delay。

本地 anvil 一键跑通：
```bash
anvil  # 终端 A
cd contracts && forge script script/Deploy.s.sol:Deploy --rpc-url http://localhost:8545 --private-key 0xac...80 --broadcast  # 终端 B
```

## liquidate 真实签名 vs 冻结签名

完全一致。后端 `vault_client.py` 不需调整。仅多了 `nonReentrant` 修饰（不影响 ABI 或 selector）。

## 已知风险与遗留

1. **没引入 `@openzeppelin/contracts-upgradeable`**：用了 OZ 标准 5.x 的 Initializable / UUPSUpgradeable（功能等价）。如果要 contracts-upgradeable，半天加 lib 就行，不影响语义。
2. **CTF 余额留在 vault**：清算后 vault 仍持有 ERC1155，账目已结算但 token 在 vault 名下。后续 sprint 加 `withdrawLiquidatedCTF(loanId, to)` admin-only 接口。
3. **旧 vault `0xB435...` wind-down**：DEPLOY.md 写明流程（用户主动 repay；或 admin pause + emergencyReturnCollateral）。需要 admin 在主网操作。
4. **后端 keeper 接入注意**：调 `liquidate` 之前**必须先把 USDC transfer 到 vault**，金额 ≥ shares × priceE6 / 1e6，否则 `InsufficientProceeds`。reason 严格大小写敏感。Gamma 返回 0 时合约 revert（要单独走 emergencyReturnCollateral 路径——本期未实现）。
5. **没动 `backend/.env`**：部署完后由用户把 `KPAX_VAULT_ADDRESS=<proxy>` 写进 `backend/.env`，重启 worker。
6. **forge 没在 agent 侧跑**：沙箱拒绝。代码全部交叉手算验证。强烈建议 pull 后立刻 `forge test -vvv`。

## 下一步（hand-off 期）

1. `cd contracts && forge build && forge test -vvv` —— 验证测试全绿
2. `forge script script/Deploy.s.sol:Deploy --rpc-url ... --broadcast`（按 DEPLOY.md）
3. 后端把 `KPAX_VAULT_ADDRESS` 切到新 proxy + 重启 worker
4. `depositLP` 注资 + 自借自测一笔
5. 旧 vault `0xB4357796...` 走 wind-down
6. **补 keeper 链下卖 CTF + USDC pre-fund 流程**（agent 风险 #4）—— 本期 plan 未覆盖，hand-off 期增量
