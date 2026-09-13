# KPAX Ball — Development Rules

## What is this

KPAX Ball is a Chrome browser extension that provides AI-powered football analysis on Polymarket. When a user browses a football market, the extension detects it and offers a quick preview + deep expert debate analysis in a side panel.

## Project structure

- `extension/` — Chrome Extension (Manifest V3, TypeScript, React, Tailwind)
- `backend/` — FastAPI API server (Python, SQLAlchemy, LiteLLM)
- `scripts/` — Data sync and verification scripts
- `PLAN.md` — Development plan with sprint breakdown

## Core principles

1. **Extension is thin, engine is thick.** The Chrome extension only does page detection + UI rendering. All analysis logic lives in the backend API. This keeps the extension lightweight and lets us reuse the API for future clients (web, mobile, other platforms).

2. **Don't rebuild what Agentxlab already has.** The debate engine, AI provider, reverse discovery, and token quota system are copied from `Agentxlab/projects/knowledge-graph/backend/app/services/`. Adapt the prompts and agent generation, but don't rewrite the core orchestration.

3. **Polymarket API first, DOM parsing second.** Always prefer the Gamma API (`gamma-api.polymarket.com`) for market data. DOM parsing is fragile and should only be a fallback. The Gamma API requires no authentication for read operations.

4. **Analysis tool, not betting advice.** Never use language like "you should buy/sell." Reports say "analysis suggests market deviation" with confidence levels and uncertainty sources. Every report includes a disclaimer.

5. **Football only (for now).** Phase 1 scope is Premier League + World Cup 2026. Don't add other sports or market types until Phase 2.

## Tech stack

### Backend
- Python FastAPI with async/await
- Pydantic v2 for validation
- SQLAlchemy 2.0 with mapped_column style
- LiteLLM for multi-model AI dispatch
- Alembic for database migrations
- Config via pydantic-settings (.env files)

### Extension
- Chrome Manifest V3
- TypeScript strict mode
- React 19 for Side Panel UI
- Tailwind CSS for styling
- Vite for bundling

## Conventions (follow Agentxlab patterns)

### Backend
- Routers in `app/routers/`, services in `app/services/`, models in `app/models/`
- All service functions are `async`
- Use `Depends(get_db)` for database sessions
- Config values via `from app.config import settings`
- SSE streaming for long-running analysis (same pattern as Agentxlab debate streaming)

### Extension
- Shared types in `src/shared/types.ts`
- API clients in `src/shared/api.ts` and `src/shared/polymarket-api.ts`
- Content Script communicates with Service Worker via `chrome.runtime.sendMessage`
- Side Panel uses React components in `src/sidepanel/components/`

### Polymarket API reference
- Gamma API (markets/events): `https://gamma-api.polymarket.com`
- CLOB API (order book/prices): `https://clob.polymarket.com`
- URL pattern: `polymarket.com/event/{slug}`
- Slug → event data: `GET /events?slug={slug}`

## Testing

- Backend: `pytest` with httpx async test client
- Extension: manual testing in Chrome with `chrome://extensions` developer mode
- After any analysis logic change, test with a real Polymarket football market slug

## Deployment

- Backend: Docker Compose (see `docker-compose.yml`)
- Extension: `npm run build:prod` → zip `dist-prod/` → upload to Chrome Web Store
- Dev: `uvicorn app.main:app --reload` (backend) + `npm run build:dev` (extension, one-shot, DEV manifest, prod URLs)

---

## Lending vault — current state (V4, deployed 2026-05-05)

**Live impl: `LendingVaultV4` at `0x25d78dc206844b567d46d51888b0990aab52bc94`.**
Behavior: borrower's EOA gets USDC.e directly on `openLoan`, repays USDC.e
directly via `vault.repay`. Keeper liquidation flow keeps V3's pUSD-via-Relayer
step-4 (PM CLOB V2 still settles in pUSD); `settleLiquidation` unwraps
pUSD → USDC.e and distributes. Vault underlying = USDC.e.

### Status as of session end 2026-05-05

**Done:**
- V4 contract written + 9/9 unit tests passing + V3→V4 storage layout
  byte-identical
- V4 deployed on Polygon mainnet, proxy upgraded via `UpgradeV4Direct`
- Backend `prepare-borrow` / `prepare-repay` rewritten to V4 (6-arg openLoan,
  USDC.e direct), `borrower-deposit-wallet` endpoint deleted
- Frontend `BorrowFlowDialog` / `RepayModal` rewritten back to direct
  USDC.e flow; deposit-wallet banner removed; `pm-relayer-client.ts` deleted
- DB cleaned: `loans` / `lending_events` / `lending_alerts` tables emptied
  (all 61 stale loan rows deleted)
- **End-to-end V4 borrow + repay verified on mainnet** ✓ (small principal,
  USDC.e flowed correctly to/from borrower EOA)

**Wreckage left from V3 experiment (don't touch):**
- 0.12 pUSD permanently locked at `0x69d55fdB976479c3eDC0a686388ddD936DCC8624`
  (V3 wrap into a CREATE2 deposit-wallet address PM no longer deploys —
  see `reference_polymarket_eip7702_wallets.md`)
- LP pool absorbed the 0.12 USDC.e loss

**Don't re-run** Phase 1 verification (`scripts/verify_pusd_pre_flight.py`)
unless the keeper PM proxy `0xa996…` somehow loses its V2 status.

### Status as of session end 2026-05-09 (next session resumes here)

**Session 主线**：admin 手动清算了 loans 67–70。链上 5 步流程跑通了，但 loan 70
在前端长期"卡在 active 又能清算"——挖出 reaper + indexer **两层** bug，全部修掉。
顺手把扩展前端从 native USDC 切到 USDC.e、加了持仓手动刷新按钮、写了 vault 杂质清理脚本。

**Done this session:**

A) **Reaper 重写** (`backend/app/worker.py` `_reap_stuck`)
   - 旧逻辑：`liquidating` 超 15min 没 confirm → 盲目回滚到 `active`，且只清
     `liquidating_at`，不清 `withdrawn_at` / `close_tx`，留下"撕裂"状态。
     Loan 70 就栽在这里：链上 settle 已成功，但 DB 被 reaper 改回 active。
   - 新逻辑：先 `eth_getTransactionReceipt(close_tx)` —— 链上 confirmed →
     调 `finalize_from_close_tx` 直接解码 `LoanLiquidated` event 写终态
     （**不**回滚）；revert/未 mined → 才回滚到 active。
   - 公共逻辑抽到 `backend/app/services/lending/loan_finalizer.py`；
     `backend/scripts/finalize_settled_loan.py` 也复用同一函数。

B) **Indexer 修复** (`backend/app/services/lending/event_indexer.py`)
   - **根本 bug：cursor 之前用 `MAX(lending_events.block_number)+1`** —
     空窗口永远不推进。fresh DB 从 deploy_block 起几千个空块就让 indexer
     永远写不进一条事件。**新表 `IndexerCursor`**（`backend/app/models/indexer_cursor.py`）
     持久化 cursor，每 tick 推进，无论是否产出事件。
   - **批次自适应**：`httpx.ReadTimeout` → batch 减半（200→100→50→25 min），
     成功后 ×2 增长回 200。publicnode 在 200 块 4-topic OR 过滤上经常超时。
   - **每 tick 日志**：`start/end/latest/batch/behind_after`。
   - 当前 cursor 已追到 latest-67 blocks（uvicorn `--reload` 自动拾到新代码
     并建表）。

C) **Loan 70 残留状态手动修复**
   - 链上 settle tx `0xb39c2cdd…df440` 已成功（block 86612270），但 DB 是
     status=active + withdrawn_at 设了 + close_tx 设了 + closed_at=None。
   - 跑 `python -m scripts.finalize_settled_loan 70` → 改成 `liquidated_ltv`，
     residual=0.831588 USDC.e，补一行 `lending_events`。

D) **前端 USDC → USDC.e 切换**
   - V4 vault underlying 是 USDC.e，但 `extension/src/shared/wallet.ts` 还在
     读 native USDC `0x3c49…`。
   - 改常量 `POLYGON_USDC_ADDRESS` → `POLYGON_USDC_E_ADDRESS`，地址 `0x2791…`。
   - `App.tsx` header + `Profile.tsx` 钱包卡片 + Polygonscan 链接全部从
     `USDC` 标签换成 `USDC.e`。
   - **保留**："1 USDC 撬动 5 USDC 仓位" 营销文案不动（i18n 里写死，泛指）。

E) **持仓手动刷新按钮**（方案 B 选型）
   - 调研结论：扩展只在 mount/切 market/自家 borrow-repay 事件时刷新；
     **用户在 PM 网页买卖不会触发任何刷新**。
   - 实现：4 张状态卡片（noPosition / unsupported / hasActive / eligible-borrow）
     右上角加 `RotateCw` 按钮 + i18n key `lendingBorrowEntryRefreshTitle`。
     按钮调已有的 `reloadAll()`，复用 `syncing` 状态。
   - **没做**（留下一 session）：方案 A（visibilitychange/focus 监听）
     + 方案 C（sidepanel 可见时 20s 节流轮询）。

F) **Vault 杂质清理脚本** (`backend/scripts/cleanup_vault_residuals.py`)
   - 链上盘点 vault `0x0f73…` 内：3.0 native USDC + 2.506518 pUSD（杂质）
     + 0.37 USDC.e（其中 0.17 是 LP 账本，0.20 是 settle 流程留的 free
     balance — 即 `usdc.balanceOf(vault) − lpPoolBalance`）。
   - 三子命令：
     - `status`：只读盘点（无需 admin 私钥）
     - `drain --yes`：`setPaused(true)` → 2× `emergencyWithdrawERC20` →
       `setPaused(false)`，把 native USDC + pUSD 转到 admin EOA
     - `deposit-lp --amount X.YY`：admin EOA 上 `usdc_e.approve(vault, X)`
       → `vault.depositLP(X)`，账本 `lpPoolBalance` 同步增加
   - 新加 `vault_admin_private_key` 配置项（`VAULT_ADMIN_PRIVATE_KEY` in
     `backend/.env`，**绝对不要**和 keeper key 混用）。
   - 安全护栏：drain 前 call `vault.admin()` 校验签名地址匹配 `0x368E55…`；
     只 drain 非 underlying 的 token（**不**碰 USDC.e，不影响 `lpPoolBalance`
     账本）；任何 tx revert 立刻 sys.exit。

**经验 / 教训（重要）：**

1. **不要让两个独立组件改同一份状态字段。** Reaper 看到 `liquidating` 超时
   就改 status，对链上一无所知；indexer 才是真理，但它跑不动。这次取最小改动：
   让 reaper 先查链上 receipt 再决定。长期更干净的方案是取消 reaper，让
   indexer 一家说了算，再加链上 timeout 监控。

2. **Indexer cursor 不能从事件表派生。** `MAX(events.block)+1` 在长空窗口
   下卡死。任何按块扫的索引器必须有独立 cursor 持久化。

3. **Public RPC 不可信。** publicnode 经常 `ReadTimeout`；需要自适应 batch
   + tick 日志能看到追块进度。长期解换 Alchemy/QuickNode key（plan §K1）。

4. **本机 IP 不在 PM 支持区域**，不能自动 curl PM CLOB / Relayer endpoints
   测试 —— 调 PM 服务直接 403 geoblock。**让用户手动操作前端**走流程，或脚本
   只调链上 RPC。

5. **PM 三类 key 不能混用：**
   - **CLOB API key**（盘口签单，绑 keeper proxy `0xa996…`，PM Settings → API 密钥）
   - **Relayer API key**（gasless wallet ops `/submit`，**绑 keeper EOA `0x8C95…`**，
     PM Settings → Relayer API 密钥）
   - **Builder/Developer key**（订单 builder_code 折扣，绑 keeper proxy `0xa996…`，
     PM Settings → 开发者码）

   之前 `.env` 把 Relayer 字段填成 Builder key (`019e05db…`)，导致 step 4
   一直 401 invalid auth。**正确的 Relayer key 是 `019df5f1-9ef5-7745-acd0-048d3ef8dec2`**。

6. **不要假设 receipt 拿不到 tx 就是没成功。** Polygonscan 维护态会显示 tx
   未 confirm，但 RPC 早就有 receipt。永远以 RPC `eth_getTransactionReceipt`
   为准。

**Next session — TODO:**

1. **重启 worker 进程** 让新 reaper 生效。`python -m app.worker` 自 Sun 11AM
   一直跑旧代码（pid 82223 没重启）。uvicorn 已经因 `--reload` 拾到新代码
   并把 `IndexerCursor` 表建好。

2. **跑 cleanup_vault_residuals 完成 vault 资产对账：**
   - 在 `.env` 加 `VAULT_ADMIN_PRIVATE_KEY=…`（admin EOA `0x368E55…` 私钥，无 0x 前缀）
   - `python -m scripts.cleanup_vault_residuals status` 看当前
   - `python -m scripts.cleanup_vault_residuals drain --yes`（admin EOA 收到
     3.0 USDC + 2.51 pUSD）
   - 在 admin EOA 上**手动** swap：3 USDC → USDC.e (Uniswap v3 0.01% 池)；
     2.51 pUSD → USDC.e（先看 Uniswap quote；没流动性就走 PM Offramp via
     keeper proxy `0xa996…`：admin → keeper EOA → keeper proxy → 调
     `Offramp.unwrap(pUSD, keeper_eoa)` → 转回 admin EOA）
   - `python -m scripts.cleanup_vault_residuals deposit-lp --amount X.YY`
     把 swap 后金额充回 LP（账本同步增加 `lpPoolBalance`）
   - 完成后 `lpPoolBalance` 应 ≈ 0.17 + 5.5 = 5.7 USDC.e

3. **测自动 keeper：** 手动清算 5 步流程已经验证通（loan 67-70 全跑通）。
   下一步开一笔小 loan，让 LTV 自动触发清算，验证 `keeper_loop` 自动捡起
   LTV 突破和 kickoff-due 的能力。

4. **PM docs 交叉核对**（一直积压）：`orderType` / `expiration` / `postOnly`
   默认值；CLOB V2 下单字段语义。

5. **持仓刷新方案 A+C**（如果还有空）：sidepanel `document.visibilitychange`
   + `window.focus` 监听 + 可见时 20s 节流轮询。让用户在 PM 网页买完几秒内
   自动刷新。

6. **V3 文件清理：** `contracts/src/LendingVaultV3.sol` 移到 `legacy/`
   子目录，commit 整个 V4 迁移 + 这次 session 的 reaper/indexer 修复。

**Vault current state (2026-05-09):**
- proxy `0x0f73…ef26`，paused=false，admin `0x368E55…`，keeper `0x8C95…`
- `nextLoanId=13`；loans 0-2 是 V2/V3 历史；3-12 是 V4 测试期 loans（对应
  DB id 67-70 + 一些 V3 testing loans）
- vault 余额: 3.0 native USDC（待 drain）+ 2.506518 pUSD（待 drain）+
  0.37 USDC.e（其中 0.17 LP，0.20 free）
- DB loan 70 已 `liquidated_ltv` 终态；69、68、67 仍是 `withdrawing`
  （V3/V4 测试时 keeper 中途崩，CTF 在 PM proxy；如果不打算回收可跑
  `scripts.fix_stuck_loan` 一键标 `liquidated_ltv` 走完账）
- IndexerCursor 表已建（uvicorn `--reload` 触发 `Base.metadata.create_all`），
  cursor 接近 latest

**新增的文件（这次 session）：**
- `backend/app/models/indexer_cursor.py`
- `backend/app/services/lending/loan_finalizer.py`
- `backend/scripts/finalize_settled_loan.py`
- `backend/scripts/cleanup_vault_residuals.py`

**修改的关键文件：**
- `backend/app/worker.py` — 新 reaper 逻辑
- `backend/app/services/lending/event_indexer.py` — IndexerCursor + 自适应 batch
- `backend/app/main.py` — 注册 IndexerCursor 模型
- `backend/app/config.py` — `vault_admin_private_key`
- `extension/src/shared/wallet.ts` — `POLYGON_USDC_E_ADDRESS`
- `extension/src/sidepanel/components/BorrowEntry.tsx` — `RefreshButton` 子组件
- `extension/src/sidepanel/components/Profile.tsx` — USDC.e 标签 + 链接
- `extension/src/sidepanel/App.tsx` — header USDC.e
- `extension/src/shared/i18n.ts` — `lendingBorrowEntryRefreshTitle`

### Critical addresses (Polygon mainnet)

```
KPAX vault proxy             0x0f738680bb060ffb01ebabb7415ce8084410ef26
keeper EOA                   0x8C95e1C75eB1355566FE9A9cc20453Cbc0639B3E
keeper PM DepositWallet      0xa996B5eE84209B7F73CE3573b71e8261547c431f
                             (pre-EIP-7702 V2 proxy — still works for keeper
                              because PM kept legacy V2 wallets live)
USDC.e (vault underlying)    0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
pUSD (PM V2 collateral)      0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB
PM CollateralOnramp          0x93070a847efEf7F70739046A929D47a521F5B8ee
PM CollateralOfframp         0x2957922Eb93258b93368531d39fAcCA3B4dC5854
PM DepositWalletFactory      0x00000000000Fb5C9ADea0298D729A0CB3823Cc07
PM DepositWallet V2 impl     0x58CA52ebe0DadfdF531Cde7062e76746de4Db1eB
                             (legacy — see EIP-7702 note below)
CTF (Polymarket)             0x4D97DCd97eC945f40cF65F87097ACe5EA0476045
V2 CTF Exchange (main)       0xE111180000d2663C0091e4f400237545B87B996B
V2 CTF Exchange (neg-risk)   0xe2222d279d744050d28e00520010520000310F59
PM Relayer API endpoint      https://relayer-v2.polymarket.com
PM EIP-7702 wallet impl      0xe6cae83bde06e4c305530e199d7217f42808555b
                             (delegate target for new PM user EOAs)
Native USDC (DEPRECATED)     0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359
```

### V3 → V4 — why we backed off the wrap-into-PM-wallet design

V3 (`LendingVaultV3`) tried to wrap USDC.e → pUSD on `openLoan` and send
the pUSD into the borrower's PM V2 DepositWallet (CREATE2-derived from
the borrower's EOA via `factory.predictWalletAddress`). That worked on
paper and passed all 16 unit tests. **It broke on Polygon mainnet** because
PM had silently migrated their wallet system from EIP-1967 CREATE2 proxies
to **EIP-7702 delegated EOAs**:

- Old (still works for accounts active during the V2 cutover):
  `predictWalletAddress(impl=0x58CA…, salt=bytes32(eoa))` →
  EIP-1967 proxy. Verified for keeper EOA `0x8C95…` → `0xa996…`.
- **New (broken)**: PM allocates a fresh EOA per user, signs an EIP-7702
  SetCode authorization delegating to `0xe6cae8…555b`, and stores the
  user_id → deposit_address mapping in their database. The address is
  **not deterministic from the user's main EOA** — KPAX cannot derive it.

When V3 borrowed for a "new" user (any EOA that hadn't activated a V2
wallet during the V2 cutover window), the wrapped pUSD landed at the
predicted CREATE2 address that PM no longer deploys → permanent lock.
Lost 0.12 pUSD on loan #2 confirming this.

V4 abandons the wrap-into-PM-wallet model. Borrower receives USDC.e on
their own EOA; depositing into PM is the borrower's manual step (PM's UI
accepts USDC.e direct, MATIC, fiat onramps, etc).

### Active loans

`nextLoanId = 3`. After the V4 cleanup:
- `#0` repaid (V2 era)
- `#1` cleaned up via `emergencyReturnCollateral` (V2 era)
- `#2` cleaned up via `emergencyReturnCollateral` (V3 wrap-into-PM-wallet
  attempt; 0.12 pUSD permanently locked at undeployable CREATE2 address)

### Active loans the next session must respect

(none — start fresh on V4)

### Watch-outs

- **Vault proxy stays at `0x0f73…ef26`.** Impl swaps in place. Per user
  direction (2026-05-05).
- **No timelock for dev.** `admin` EOA == `upgrader`. Single-tx upgrades
  via `UpgradeV4Direct.s.sol`.
- **Don't try to derive PM deposit addresses on-chain.** PM moved to
  EIP-7702 delegated EOAs and the mapping is in PM's database, not
  derivable from the user's EOA. If you need the user's PM deposit
  address, ask the user to paste it (or just send USDC.e to their main
  EOA and let them deposit themselves — V4's design).
- **Onramp accepts USDC.e only**; Native USDC is rejected. V3
  `initializeV3` already migrated `vault.usdc` → USDC.e, V4 keeps that.
- **`pm_relayer.py` still relevant for keeper.** Step 4 of liquidation
  uses keeper's V2 DepositWallet (`0xa996…`) to transfer pUSD to vault.
  PM kept the legacy keeper wallet alive so this still works.
- **`docs/kpax-vault-pusd-migration-plan.md` is historical** — kept for
  the rationale, but the in-flight V3 design described there is dead.
  V4 is the live design.
