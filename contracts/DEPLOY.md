# 部署 LendingVault 到 Polygon 主网（Sprint 4 + UUPS proxy）

> 这是新的 UUPS-proxy 版部署流程。旧的 non-proxy vault `0xB435779651c78d27D40f3e88a9674aeAA6Acd08F` **不动**：灰度阶段如果还有活跃 loan，必须先 wind down（要么用户主动 repay，要么 admin 走 `emergencyReturnCollateral` 把抵押退回 proxy 后弃用）。新前端 / 后端切到下面新部署的 proxy 地址。

## 0. 前置检查

| 项 | 你需要 |
|---|------|
| Foundry | `forge --version` 能跑 |
| 部署者 EOA | 你的 MetaMask 地址，例如 `0xAA35E5045783e2f9CA553cAD9545BaF213998Ab1` |
| MATIC 余额 | ≥ 0.5（多部署一个 timelock + proxy） |
| Multisig 地址（可选） | 2-of-3 Safe 地址；没有的话用 EOA 占位，后面再切 |
| Polygonscan API Key（可选） | 自动 verify 用 |

## 1. 一次性：Foundry keystore

```bash
cast wallet import deployer --interactive
# 提示 "Enter private key:" 时粘贴 0x... 64 字符私钥
# 提示 "Enter password:" 时设个本机解密用的密码
cast wallet address --account deployer  # 验证
```

## 2. 部署（implementation + timelock + proxy 一条龙）

```bash
cd contracts

# 不带 multisig：默认用 broadcaster 自己当 timelock 的 proposer/executor
forge script script/Deploy.s.sol:Deploy \
  --rpc-url https://polygon-bor-rpc.publicnode.com \
  --account deployer \
  --sender 0xAA35E5045783e2f9CA553cAD9545BaF213998Ab1 \
  --broadcast \
  -vvv

# 带 multisig（推荐）：
MULTISIG_ADDRESS=0xYourSafe \
ADMIN_ADDRESS=0xYourSafe \
KEEPER_ADDRESS=0xYourKeeperEOA \
forge script script/Deploy.s.sol:Deploy \
  --rpc-url https://polygon-bor-rpc.publicnode.com \
  --account deployer \
  --sender 0xAA35E5045783e2f9CA553cAD9545BaF213998Ab1 \
  --broadcast \
  -vvv
```

输出会列三个地址：

```
impl deployed at:     0x.....   <- bytecode 实现，**不要**当成 vault 用
timelock deployed at: 0x.....   <- governance only
proxy deployed at:    0x.....   <- 这才是 vault，所有调用打这个地址
```

`KPAX_VAULT_ADDRESS` 用 **proxy** 地址。

## 3. 切换 backend 的 vault 地址

> 合约工程师不动 `backend/.env`，由后端工程师执行：

```bash
# backend/.env 里把 KPAX_VAULT_ADDRESS 换成上面的 proxy 地址
# 然后重启 uvicorn
```

如果旧 vault 还在被前端引用，建议：

1. 后端先把 `KPAX_VAULT_ADDRESS` 切到新 proxy。
2. 前端缓存 / Privy redirect 不需要改。
3. 用户的 `setApprovalForAll(oldVault, true)` 在新 vault 上无效，需要重新做一次给新 proxy。

## 4. （部署后）注资 LP 池 + 自借自测

```bash
VAULT=0x<proxy 地址>
USDC=0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359

cast send $USDC "approve(address,uint256)" $VAULT 3000000 \
  --rpc-url https://polygon-bor-rpc.publicnode.com \
  --account deployer

cast send $VAULT "depositLP(uint256)" 3000000 \
  --rpc-url https://polygon-bor-rpc.publicnode.com \
  --account deployer

cast call $VAULT "lpPoolBalance()(uint256)" \
  --rpc-url https://polygon-bor-rpc.publicnode.com
```

## 5. 升级流程（24h timelock）

实现合约要换新版本？两步：

### 5a. 部署新 implementation

```bash
forge create src/LendingVault.sol:LendingVault \
  --rpc-url https://polygon-bor-rpc.publicnode.com \
  --account deployer
# 记下输出的 Deployed to: 0xNEW_IMPL
```

### 5b. Schedule（提案）

如果 timelock 的 proposer 是 EOA，本地 forge script 跑：

```bash
TIMELOCK=0x<timelock 地址>
PROXY=0x<proxy 地址>
NEW_IMPL=0x<新 impl 地址>

forge script script/Upgrade.s.sol:UpgradeSchedule \
  --sig "run(address,address,address)" \
  $TIMELOCK $PROXY $NEW_IMPL \
  --rpc-url https://polygon-bor-rpc.publicnode.com \
  --account deployer \
  --broadcast
```

如果 proposer 是 Safe multisig：

1. 在本地 abi-encode 一份 schedule 的 calldata
2. 在 Safe UI 提交，由 2/3 owner 签名 → Safe 调 `timelock.schedule(...)`

### 5c. 等 24h，然后 Execute

```bash
forge script script/Upgrade.s.sol:UpgradeExecute \
  --sig "run(address,address,address)" \
  $TIMELOCK $PROXY $NEW_IMPL \
  --rpc-url https://polygon-bor-rpc.publicnode.com \
  --account deployer \
  --broadcast
```

或 Safe UI 同样的流程。执行后 `vault.upgradeToAndCall(NEW_IMPL, "")` 触发，proxy 切到新实现，所有 storage 保留。

---

## 5'. Sprint 4 V2 升级（一次性 batch：setUpgrader + upgradeToAndCall）

V1 → V2 升级是一次特殊的合并 batch：把 `upgrader` 角色从 timelock 切回 admin EOA（K18：开发期方便迭代），同时 swap 实现到 V2。两个调用打包成一个 `scheduleBatch`，原子执行。

### 5'a. 部署 V2 implementation

```bash
cd contracts

forge script script/DeployV2.s.sol:DeployV2 \
  --rpc-url https://polygon-bor-rpc.publicnode.com \
  --account deployer \
  --sender 0xYourEOA \
  --broadcast \
  --verify --etherscan-api-key $POLYGONSCAN_KEY \
  -vvv
# 记下输出的 V2 implementation 地址 → V2_IMPL
```

### 5'b. Schedule batch（admin EOA 当 timelock proposer）

```bash
TIMELOCK=0x<timelock 地址>     # 同 V1 部署日志里的 timelock
PROXY=0x40892387747Da2f46fecaC902274081F676F1592
V2_IMPL=0x<上一步输出>
NEW_UPGRADER=0x<admin EOA>      # K18：把 upgrader 切给 admin

forge script script/ScheduleV2Upgrade.s.sol:ScheduleV2Upgrade \
  --sig "run(address,address,address,address)" \
  $TIMELOCK $PROXY $V2_IMPL $NEW_UPGRADER \
  --rpc-url https://polygon-bor-rpc.publicnode.com \
  --account deployer \
  --sender 0xYourEOA \
  --broadcast \
  -vvv
```

输出会打印 `operationId`，记下来 — 24h 后 execute 时如果想 cancel / 查询都需要。

> Salt 固定为 `keccak256("KPAX_V2_UPGRADE_BATCH")`，schedule / execute 两次脚本都用同一个 salt，确保第二次能定位到原 batch。

### 5'c. 等 24h，Execute

```bash
forge script script/ExecuteV2Upgrade.s.sol:ExecuteV2Upgrade \
  --sig "run(address,address,address,address)" \
  $TIMELOCK $PROXY $V2_IMPL $NEW_UPGRADER \
  --rpc-url https://polygon-bor-rpc.publicnode.com \
  --account deployer \
  --sender 0xYourEOA \
  --broadcast \
  -vvv
```

执行成功后：
- `vault.upgrader() == NEW_UPGRADER`（admin EOA），后续 admin 直接 `upgradeToAndCall` 无需 timelock delay
- `vault.treasury() == 0x0`（仍未设置）

### 5'd. Admin 收尾配置

```bash
PROXY=0x40892387747Da2f46fecaC902274081F676F1592
TREASURY=0xcb7A9e4F9366de25F36195Ce96625bE3dEe05bb7

# K14：设置 treasury
cast send $PROXY \
  "setTreasury(address)" $TREASURY \
  --rpc-url https://polygon-bor-rpc.publicnode.com \
  --account deployer

# 验证
cast call $PROXY "treasury()(address)" \
  --rpc-url https://polygon-bor-rpc.publicnode.com
```

```bash
# Keeper EOA 一次性 approve（returnCtfFromKeeper 需要）
KEEPER=0x<keeper EOA>
CTF=0x4D97DCd97eC945f40cF65F87097ACe5EA0476045

cast send $CTF \
  "setApprovalForAll(address,bool)" $PROXY true \
  --rpc-url https://polygon-bor-rpc.publicnode.com \
  --account keeper

# 验证
cast call $CTF "isApprovedForAll(address,address)(bool)" $KEEPER $PROXY \
  --rpc-url https://polygon-bor-rpc.publicnode.com
```

### 5'e. 链上完整性自检

```bash
PROXY=0x40892387747Da2f46fecaC902274081F676F1592

cast call $PROXY "admin()(address)"        --rpc-url https://polygon-bor-rpc.publicnode.com
cast call $PROXY "keeper()(address)"       --rpc-url https://polygon-bor-rpc.publicnode.com
cast call $PROXY "upgrader()(address)"     --rpc-url https://polygon-bor-rpc.publicnode.com  # 应为 NEW_UPGRADER
cast call $PROXY "treasury()(address)"     --rpc-url https://polygon-bor-rpc.publicnode.com  # 应为 0xcb7A...
cast call $PROXY "lpPoolBalance()(uint256)" --rpc-url https://polygon-bor-rpc.publicnode.com
cast call $PROXY "nextLoanId()(uint256)"    --rpc-url https://polygon-bor-rpc.publicnode.com
```

`lpPoolBalance` / `nextLoanId` 应与升级前一致。

### 5'f. （可选）后续生产化：upgrader 切回 timelock

V2 上线、灰度演练通过后，admin 可以再调一次 `setUpgrader(timelock)`（这次没 24h delay 因为 admin 现在就是 upgrader），把治理路径切回 timelock 24h delay。MVP 阶段先不做。

## 6. 切换 timelock proposer/executor 到真 multisig

如果部署时用 EOA 当占位 multisig，后面切真 Safe：

```bash
# Safe 必须先有 PROPOSER_ROLE。原 EOA 给它 grantRole：
TIMELOCK=0x<timelock 地址>
PROPOSER_ROLE=$(cast keccak "PROPOSER_ROLE")
EXECUTOR_ROLE=$(cast keccak "EXECUTOR_ROLE")
CANCELLER_ROLE=$(cast keccak "CANCELLER_ROLE")
NEW_SAFE=0xYourSafe

# 这本身需要 timelock 自己执行，所以走 schedule → wait → execute 三步：
# 1. Schedule: timelock.schedule(timelock, 0, encodeCall(grantRole, (ROLE, NEW_SAFE)), 0, 0, 24h)
# 2. 等 24h
# 3. Execute 同样的 calldata
# 4. 重复 grantRole 给 EXECUTOR_ROLE / CANCELLER_ROLE
# 5. 最后 revokeRole 把原 EOA 的 PROPOSER/EXECUTOR/CANCELLER 都拿掉

# 简单写法（每个 role 一笔）：
cast abi-encode "grantRole(bytes32,address)" $PROPOSER_ROLE $NEW_SAFE
# 把 selector 加上：cast sig "grantRole(bytes32,address)" → 0x2f2ff15d
# 完整 calldata = 0x2f2ff15d + encoded args
```

> 实际操作建议：写个 OZ Defender / Safe TX builder 的 batch；或直接用一个临时的 admin script 把 grantRole/revokeRole 串起来通过 timelock 提交。

## 故障排查

- `Insufficient balance for gas` → 转些 MATIC 到部署者
- `InvalidInitialization` → proxy 已经初始化过了（这是预期，不要再调 initialize）
- `Verify failed` → 加 `--etherscan-api-key $POLYGONSCAN_KEY`，或手动到 polygonscan 上传源码（impl + proxy + timelock 三个都要 verify）
- `nonce mismatch` → `--legacy` 试一下；或 MetaMask 该账户重置 nonce

## 旧 vault wind-down 备忘

旧 vault `0xB435779651c78d27D40f3e88a9674aeAA6Acd08F` 是 non-proxy 部署，没有 timelock，liquidate 是 stub。处理方式：

1. **不再开新 loan**：前端切到新 proxy 地址，旧 vault 不会被新 borrow 调用。
2. **存量 loan**：让用户主动 repay；或 admin `setPaused(true)` + `emergencyReturnCollateral(loanId)` 把 CTF 退回原 proxy（debt 写为 0，LP 损失等于 principal）。
3. **存量 LP**：admin `emergencyWithdrawERC20(USDC, admin, balance)` 把池子里的 USDC 拉回，之后再注入新 vault。

完整 wind-down 跑完再正式弃用旧 vault 地址。
