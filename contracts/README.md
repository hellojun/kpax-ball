# KPAX Lending — 合约

Polygon 主网的 `LendingVault` 合约。用户抵押 Polymarket CTF (ERC-1155) 份额，借出 USDC。

## Sprint 1 状态

- [x] Foundry 项目骨架
- [x] `LendingVault.sol`：storage + events + `openLoan` + `repay` (full) + `debtOf` view
- [x] Mock USDC / CTF / Exchange 测试脚手架
- [x] 8 个单测覆盖 openLoan / debtOf / repay / admin gating
- [ ] `liquidate` 实现（Sprint 2）
- [ ] Polymarket Exchange 真接口（Sprint 3）
- [ ] Mumbai 测试网部署（本 Sprint 结束前）

## 首次设置

Foundry 未安装时：

```bash
curl -L https://foundry.paradigm.xyz | bash
foundryup
```

安装依赖：

```bash
cd contracts
forge install OpenZeppelin/openzeppelin-contracts --no-commit
forge install foundry-rs/forge-std --no-commit
```

## 常用命令

```bash
# 编译
forge build

# 跑所有测试
forge test -vv

# 跑单个测试
forge test --match-test test_OpenLoan_Success -vvv

# 覆盖率
forge coverage

# fuzz 扩展运行次数
forge test --fuzz-runs 10000

# 部署到 Mumbai
cp .env.example .env         # 然后填入 RPC + deployer key
source .env
forge script script/Deploy.s.sol --rpc-url $MUMBAI_RPC_URL --broadcast -vvv
```

## 目录结构

```
contracts/
├── foundry.toml
├── remappings.txt           （由 forge install 生成，或 foundry.toml 接管）
├── src/
│   ├── LendingVault.sol
│   └── interfaces/
│       └── IPolymarketExchange.sol
├── test/
│   ├── LendingVault.t.sol
│   └── mocks/
│       ├── MockUSDC.sol
│       ├── MockCTF.sol
│       └── MockExchange.sol
└── script/
    └── Deploy.s.sol
```

## 关键参数（与后端 `app/services/lending/config.py` 保持一致）

| 常量 | 值 | 含义 |
|------|-----|------|
| `APR_BPS` | 1200 | 12% 年化 |
| `LIQUIDATION_PENALTY_BPS` | 200 | 强平罚金 2% |
| `LIQUIDATION_LTV_BPS` | 8000 | 强平线 80% |
| `KICKOFF_BUFFER` | 2h | 开赛前强平窗口 |
| `SECONDS_PER_YEAR` | 31_536_000 | 利息计算分母 |

**修改这些常量必须同时更新 `backend/app/services/lending/config.py`，并重新跑
Python ↔ Solidity 的对齐测试（Sprint 2 引入）**。

## 审计与安全

Sprint 4 前必须：
1. 签约审计方（OpenZeppelin / ConsenSys Diligence，预算 $10–20K）
2. 把 `admin` 切到 2-of-3 multisig + 48h timelock
3. 启用 Immunefi 漏洞赏金池（$5K）
4. 属性测试：`forge test --fuzz-runs 50000`
