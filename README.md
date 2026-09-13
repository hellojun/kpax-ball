# KPAX Ball

> 在 Polymarket 上做足球盘口 AI 分析的 Chrome 扩展，并支持以 Polymarket 仓位抵押借出 USDC。

KPAX Ball 由两条产品线组成。**AI 分析**：当你在 Polymarket 浏览足球盘口时，扩展会自动识别当前比赛，
在侧边栏给出快速预览和多专家辩论式深度分析，并对比市场隐含概率与 KPAX 模型概率。
**KPAX Lending**：无需卖出手中的 Polymarket CTF 份额，即可抵押借出 USDC，由 AI 按球队波动性、
开赛时间与联赛流动性给出建议 LTV，并在开赛前自动平仓以规避 in-play 跳空风险。

## 功能

- 盘口自动识别与数据提取（Gamma / CLOB / Data API）
- 快速预览与多专家辩论深度分析，SSE 流式输出
- 知识图谱可视化（Zep）与赛后结果回验
- 抵押借贷：仓位读取、风险评估、分层 LTV、借款与还款流程
- Keeper 自动清算 + 链上事件索引 + 预警引擎
- Privy 登录（Google 等），JWT 会话

## 仓库结构

| 目录 | 说明 |
| --- | --- |
| `extension/` | Chrome 扩展（Manifest V3、TypeScript、React、Tailwind、Vite、viem） |
| `backend/` | FastAPI 服务（SQLAlchemy async、Alembic、LiteLLM、web3、py-clob-client） |
| `contracts/` | Solidity 合约（Foundry），UUPS 可升级 `LendingVault` V1 至 V4 |
| `auth-page/` | 独立 Privy 登录页（扩展内无法直接完成 OAuth 流程） |
| `scripts/` | 数据同步、赛后回验、凭据派生等脚本 |
| `docs/` | 产品与技术文档、隐私政策、商店素材 |
| `design/` | 界面原型 |

**架构原则：扩展薄、引擎厚。** 扩展只负责页面识别与 UI 渲染，全部分析逻辑位于后端，
以便未来复用到 Web、移动端等其他客户端。

## 技术栈

- 前端：TypeScript、React、Tailwind CSS、Vite、viem
- 后端：Python 3.11+、FastAPI、SQLAlchemy(async)、Alembic、LiteLLM、PostgreSQL / SQLite
- 合约：Solidity、Foundry、OpenZeppelin UUPS，部署于 Polygon
- 外部服务：Polymarket Gamma / CLOB / Relayer、Privy、Zep、API-Football、智谱 AI

## 快速开始

前置依赖：Node.js 18+、Python 3.11+、Foundry（仅开发合约时需要）。

### 后端

```bash
cd backend
cp .env.example .env          # 填入所需的 API Key
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload --port 8003
```

### 扩展

```bash
cd extension
npm install
npm run build:dev             # 输出 dist-dev/，连接开发后端
npm run build:prod            # 输出 dist-prod/，连接生产后端
```

在 `chrome://extensions` 打开开发者模式，选择「加载已解压的扩展程序」，指向 `dist-dev/`。

### 登录页

```bash
cd auth-page
npm install && npm run dev    # 默认 http://localhost:3100
```

### 合约

```bash
cd contracts
forge install
forge build && forge test -vv
```

### Docker

```bash
docker-compose up -d          # postgres + backend + worker
```

## 环境变量

完整清单见 `backend/app/config.py`（共 54 项），核心变量如下：

| 变量 | 说明 |
| --- | --- |
| `DATABASE_URL` | 数据库连接串，留空则使用本地 SQLite |
| `ZHIPUAI_API_KEY` | LLM 密钥 |
| `POLYGON_RPC_URL` | Polygon RPC 节点 |
| `KPAX_VAULT_ADDRESS` | LendingVault 合约地址 |
| `KEEPER_PRIVATE_KEY` | Keeper EOA 私钥，仅用于清算交易 |
| `VAULT_ADMIN_PRIVATE_KEY` | 合约管理员私钥，与 Keeper 分离 |
| `POLYMARKET_API_KEY` / `_SECRET` / `_PASSPHRASE` | CLOB 接口凭据 |
| `PRIVY_APP_ID` / `PRIVY_APP_SECRET` | Privy 认证 |
| `KPAX_JWT_SECRET` | 会话签名密钥 |
| `API_FOOTBALL_KEY`、`ZEP_API_KEY`、`SENTRY_DSN` | 可选服务 |

> 所有密钥一律通过环境变量注入，切勿写入代码或提交进仓库。
> 私钥请使用独立的、仅存放必要资金的热钱包，不要使用多签或金库私钥。

## API 概览

```text
GET  /health
POST /api/auth/privy/exchange      GET  /api/auth/me
GET  /api/market/detect            GET  /api/market/data
POST /api/analysis/preview         POST /api/analysis/deep
POST /api/analysis/followup
GET  /api/lending/config           GET  /api/lending/positions
POST /api/lending/prepare-borrow   POST /api/lending/confirm-borrow
POST /api/lending/prepare-repay    POST /api/lending/confirm-repay
POST /api/lending/risk-assessment  GET  /api/lending/loans
```

服务启动后可访问 `/docs` 查看完整 OpenAPI 文档。

## 测试

```bash
cd backend && pytest            # 后端
cd extension && npm test        # 扩展（vitest）
cd contracts && forge test -vv  # 合约
```

## 免责声明

本项目仅供研究与教育用途，不构成投资建议。链上借贷与预测市场交易存在本金损失风险，
包括但不限于智能合约漏洞、清算滑点与预言机故障，请自行评估风险。

## License

待补充。
