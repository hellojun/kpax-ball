# KPAX Lending — Keeper + Alert 服务开发计划

> **状态**：Plan v0.2
> **日期**：2026-04-29
> **依赖**：`docs/kpax-lending-dev-plan.md` Sprint 3、`backend/app/services/lending/price_watcher.py`、`LendingVault.sol`（已部署 Polygon 主网，**本期合约工程师并行做真实化 + UUPS proxy，详见 `contracts/REPORT.md`**）
> **预计周期**：5–7 个工作日（后端，不含合约工作与外部审计）

## 变更日志

- v0.2 (2026-04-29)：合约范围从"不做"调整为"并行 track"。`liquidate` 接口冻结为 `(uint256 loanId, string reason, uint256 currentPriceE6)`，keeper 链下传价。Day 5 验收用 mock vault 集成测试替代主网灰度，主网灰度移到合约 ready 后的 hand-off 期。
- v0.1：初稿。

---

## 0. 摘要

把 lending feature 从 “只能借不会被强平” 的半成品补成完整闭环。落地 4 个常驻协程：

1. **`event_indexer_loop`** — 订阅 `LendingVault` 链上事件落库，是其它三个 loop 的真相源
2. **`keeper_loop`** — 30 秒扫一次活跃 loan，命中 LTV/Kickoff 阈值就发清算交易
3. **`alert_engine_loop`** — 60 秒扫一次，幂等推送 70%/80%/kickoff-4h/kickoff-2h 预警
4. **（已存在）`price_watcher`** — 当前是 on-demand 调用，本期沿用，不改 WebSocket（Phase 1.5 再升级）

完成后达到 dev-plan §9 Sprint 3 的 Done Gate：100 笔模拟清算零失败、坏账率 < 2%、内部 3–5 人能完整走通。

---

## 1. 范围

### 做
- 4 个后台 loop + lifespan 接入
- 清算交易构造、签名、广播、重试（EIP-1559 替换交易）
- Chrome 通知端：Service Worker 轮询 `/api/lending/alerts/pending`
- Sentry 接入 + 关键告警指标
- 集成测试：mock vault + 真实 DB 走通幂等
- Polygon 主网灰度演练（本仓库 vault 地址，已部署）

### 不做（划清边界）
- WebSocket 价格源（保留 60s REST 缓存）
- Permissionless 清算 / 清算者奖励
- Web Push（VAPID）— 用 chrome.notifications 轮询足够
- Grafana 看板与 PagerDuty 接线（Sprint 4 运维任务，本期只埋指标）
- **主网灰度演练**（推迟到合约期 ready 后，本期用 mock vault 替代）

### 并行 track（合约工程师，本期不在后端人日内）
- `LendingVault.liquidate` stub → 真实清算逻辑（账目结算 + 残值回 borrower），接口冻结见 §4 K9
- UUPS proxy 化 + 24h timelock + 2/3 multisig admin
- 旧 vault `0xB4357796...` 弃用 / wind down，新 proxy 部署后由用户手动切 `KPAX_VAULT_ADDRESS`
- 100 笔模拟清算压测（Foundry fork）
- 详见 `contracts/REPORT.md`（合约 agent 完工后产出）

---

## 2. 架构

**进程拆分**：web 与 worker 独立进程，同一份 image，不同 entrypoint：

```
┌─ web 容器 ─────────────────────┐    ┌─ worker 容器 ───────────────────┐
│ uvicorn app.main:app           │    │ python -m app.worker            │
│  · FastAPI 路由                 │    │  asyncio.gather(                │
│  · lifespan 不再起 loop         │    │    event_indexer_loop(),        │
│                                │    │    keeper_loop(),               │
│                                │    │    alert_engine_loop(),         │
│                                │    │  )                              │
└────────┬───────────────────────┘    └────────┬───────────────────────┘
         │                                     │
         └──────── 共享 Postgres ──────────────┘
                  共享 settings / models / db.py
```

**为什么拆**：
- web 重启不打断清算扫描（反之亦然）
- web 可水平扩容，worker 保持单实例
- Sentry 标签天然区分 `service=web|worker`
- worker 资源（CPU/内存）独立调度

**worker 内 3 个 loop 仍跑在同一个事件循环**（不再拆 3 个进程）：indexer 写、keeper 读、alert 读 — 紧耦合，跨进程没好处。

### 2.1 真相源分工

| 关心的事 | 信源 | 落地位置 |
|---|---|---|
| 当前 LTV | `price_watcher.get_current_price()` × 链上价值快照 | 内存计算，不入库 |
| loan 是否还活着 | `LendingEvent` 解析后写回 `Loan.status` | DB |
| 该不该再发预警 | `LendingAlert (loan_id, alert_type)` 唯一索引 | DB |
| 清算交易状态 | EventIndexer 收到 `LoanLiquidated` 才把 `status` 从 `liquidating` → `liquidated_*` | DB |

**核心原则**：DB `status` 永远跟随链上事件，不在发交易那一刻乐观写入。`keeper_loop` 只能写过渡态 `liquidating`，最终态由 `event_indexer_loop` 收到事件后写入。

### 2.2 单实例保护

worker 启动时立刻拿一个 Postgres advisory lock：

```python
async with engine.begin() as conn:
    got = await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})
    if not got.scalar():
        logger.error("another worker holds the advisory lock; exiting")
        sys.exit(1)
```

`LOCK_KEY` 用一个固定大整数（如 `0x4B504158_4B454550`，即 `"KPAXKEEP"` 的 hex）。这样：
- 本地误开两个 worker → 第二个秒退
- k8s rolling deploy → 新 pod 启动失败直到旧 pod 释放（或加 readiness probe + maxSurge=0）
- web 容器误启 worker 入口 → 同样秒退

未来真要多副本（Phase 1.5）改成 leader election + 阈值分片，本期不做。

---

## 3. 任务拆解

每项标了责任层 + 预估人日（D = day）。

### Day 1 · 链上读写基础

- [ ] **[后端] `vault_client.py`**（0.5D）
  - 封装 `web3.py` AsyncWeb3，从 `settings.polygon_rpc_url` + `settings.kpax_vault_address` 加载
  - 暴露 `loans(loan_id)` / `debtOf(loan_id)` / `liquidate(loan_id, reason, current_price_e6)` 调用
  - **接口冻结**（与合约工程师契约）：`liquidate(uint256 loanId, string reason, uint256 currentPriceE6)`，价格精度 e6（`price × 1_000_000`），`reason ∈ {"ltv_breach", "kickoff_due"}`
  - Keeper 私钥从 `settings.keeper_private_key` 读（开发环境 .env，生产 KMS — 本期先 .env，Sprint 4 切 KMS）
- [ ] **[后端] `event_indexer.py`**（1D）
  - `last_seen_block` 持久化到一个 `key_value` 小表或 `lending_events` 取 `MAX(block_number)`
  - 每 5s `eth_getLogs(fromBlock=last+1, toBlock="latest", address=vault)`
  - 解码靠现有 `event_decoder.py`，写 `LendingEvent`（唯一索引 `(tx_hash, log_index)` 自动幂等）
  - 解析后回写 `Loan.status`：
    - `LoanOpened` → 找 `Loan(status="pending", open_tx_hash=…)` 改 `active` + 填 `onchain_loan_id`
    - `LoanRepaid` → `repaid` + `closed_at` + `total_interest_paid`
    - `LoanLiquidated` → `liquidated_ltv` 或 `liquidated_kickoff`（按事件 `reason` 字段）+ `residual_to_user` + `exit_price`
  - 异常路径：解码失败但事件已落库，不阻塞下一轮，发 Sentry warning

### Day 2 · Keeper

- [ ] **[后端] `keeper.py`**（1D）
  - 主循环结构：
    ```python
    async def keeper_loop():
        while True:
            try:
                await _scan_kickoff_due()
                await _scan_ltv_breach()
            except Exception:
                logger.exception("keeper tick failed")
                sentry_sdk.capture_exception()
            await asyncio.sleep(KEEPER_INTERVAL_SECONDS)  # 30
    ```
  - `_scan_kickoff_due`: `WHERE status='active' AND match_kickoff_at <= now+2h`
  - `_scan_ltv_breach`: 取所有 active loan，按 `(slug, ctf_token_id)` 去重批量调 `price_watcher.get_current_price`，逐笔算 LTV
  - 命中 → `_trigger_liquidation(loan, reason)`，伪码：
    ```python
    async with db.begin():
        row = await db.execute(
            select(Loan).where(Loan.id == loan.id).with_for_update()
        )
        loan = row.scalar_one()
        if loan.status != "active":
            return  # 已被处理
        tx_hash = await vault_client.liquidate(
            loan.onchain_loan_id,
            reason,                         # "ltv_breach" | "kickoff_due"
            current_price_e6=int(price * 1_000_000),
        )
        loan.status = "liquidating"
        loan.close_tx_hash = tx_hash
    ```
  - **新增 loan status：`liquidating`**（pending → active → liquidating → liquidated_*）。需要 alembic migration 改 `LOAN_STATUSES` 文档常量；DB 列是 `String(32)`，不需要 schema 改动

- [ ] **[后端] Gas 策略**（0.5D，写在 `vault_client.py`）
  - EIP-1559：`maxPriorityFeePerGas = 30 gwei`（可调），`maxFeePerGas = baseFee × 2 + tip`
  - 60s 后仍 pending → 用同 nonce 替换交易，tip × 2（最多 3 次）
  - 失败 nonce 或 revert → 回滚 `Loan.status` = `active`，记 `keeper.liquidation_failures` 指标，连续 3 笔触发告警

### Day 3 · Alert Engine

- [ ] **[后端] `alert_engine.py`**（0.75D）
  - 主循环 60s
  - 单次扫描产出 candidates list of `(loan_id, alert_type)`：
    - `kickoff_4h` / `kickoff_2h`：`match_kickoff_at` 距 now 在 [2h, 4h] / (0, 2h]
    - `ltv_70` / `ltv_80`：从 `price_watcher` 拿当前价计算 LTV，命中阈值
  - 幂等写入：`INSERT INTO lending_alerts (loan_id, alert_type) VALUES (…) ON CONFLICT DO NOTHING RETURNING id`
  - 拿到 RETURNING id 才说明是新预警 → 投递到一个 “待推送” 队列（暂用 DB 标记 `delivered_at NULL`，新增列）
  - **`LendingAlert` 模型新增列**：`delivered_at: datetime | None`，alembic migration 一条
- [ ] **[后端] `GET /api/lending/alerts/pending`**（0.25D）
  - 当前用户 `WHERE delivered_at IS NULL`，返回未推送列表
  - 客户端 ack 后 `POST /api/lending/alerts/ack` 把 `delivered_at = now()`
  - 不在服务端做 push，纯 pull —— 配合 Service Worker 轮询

### Day 4 · 前端通知 + Worker 进程

- [ ] **[前端] Service Worker `lending-notifications.ts`**（0.5D）
  - 用户登录后注册 `chrome.alarms.create('lending-poll', { periodInMinutes: 1 })`
  - alarm 触发 → 调 `/api/lending/alerts/pending` → 每条 `chrome.notifications.create`，icon 用品牌色（emerald → cyan SVG）
  - 通知点击 → `chrome.tabs.create({ url: extension sidepanel? }`)，跳到 Profile 借贷 tab
  - 通知文案 4 套（i18n key 已存在或新增）：
    - `lendingAlertLtv70` / `lendingAlertLtv80` / `lendingAlertKickoff4h` / `lendingAlertKickoff2h`

- [ ] **[后端] `app/worker.py` 入口**（0.5D）
  ```python
  # python -m app.worker
  async def main():
      _acquire_advisory_lock_or_exit()
      sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.env)
      tasks = [
          asyncio.create_task(event_indexer_loop(), name="indexer"),
          asyncio.create_task(keeper_loop(),         name="keeper"),
          asyncio.create_task(alert_engine_loop(),   name="alert"),
      ]
      await _wait_for_shutdown_signal()
      # 关停顺序：keeper（不再发新清算）→ indexer（让在飞事件落库）→ alert
      for t in [tasks[1], tasks[0], tasks[2]]:
          t.cancel()
      await asyncio.gather(*tasks, return_exceptions=True)

  if __name__ == "__main__":
      asyncio.run(main())
  ```
  - `app/main.py` lifespan **不再启动任何 loop**
  - SIGTERM / SIGINT 触发优雅关停（k8s 给 30s grace period 足够）

- [ ] **[运维] docker-compose 加 worker 服务**（0.25D）
  ```yaml
  worker:
    build: { context: ./backend, dockerfile: Dockerfile }
    command: python -m app.worker
    environment:
      DATABASE_URL: postgresql://kpax:kpax_dev@db:5432/kpax_ball
    env_file: [./backend/.env]
    depends_on:
      db: { condition: service_healthy }
    restart: unless-stopped
  ```
  - 同一个 image，仅 command 不同
  - `restart: unless-stopped` 保证 crash 后自动拉起（advisory lock 释放靠 PG 感知连接断开，秒级生效）

- [ ] **[运维] Sentry**（0.25D）
  - web 与 worker 都 init，靠 `environment` + `server_name` 区分
  - worker 内每个 loop 用 `sentry_sdk.set_tag("loop", "keeper")` 做更细标签
  - `loan_id` 作 extra context

### Day 5 · 测试

- [ ] **[后端] 单元测试**（0.5D）
  - `tests/test_keeper.py`：mock `vault_client` + `price_watcher`，验证：
    - LTV < 阈值不触发；= 阈值触发；> 阈值触发
    - 重复 tick 同一 loan 不重复发交易（`with_for_update` + `status != active` 短路）
    - kickoff 边界（2h 整 / 2h-1s / 2h+1s）
  - `tests/test_alert_engine.py`：
    - `(loan_id, alert_type)` 唯一约束生效，重启 loop 不重发
    - `delivered_at` 流程
  - `tests/test_event_indexer.py`：
    - `LoanOpened` 把 `pending` → `active`
    - `LoanLiquidated` 解析 `reason` 字段映射到正确终态
    - `(tx_hash, log_index)` 重复事件不重复落库

- [ ] **[集成] mock vault 集成测试**（1D）
  - 用 `tests/conftest.py` 内的 `FakeVaultClient` 替代真实 `vault_client`，行为：返回伪 tx_hash + 同步 emit `LoanLiquidated` 落 `LendingEvent`
  - 验证完整闭环：keeper 30s tick → 60s indexer 落库 → status = `liquidated_ltv` → SW 收到通知
  - kickoff 路径：人工把 `match_kickoff_at` 调到 now+1.5h，确认下个 tick 触发 `kickoff_2h` 清算
  - **主网灰度演练推迟到合约 ready 后**（hand-off 期任务，详见 §9）

- [ ] **[合约] 100 笔并发清算压测**（合约工程师并行 track，详见 `contracts/REPORT.md`）

---

## 4. 关键决策

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| K1 | 价格源 | 沿用 `price_watcher` REST 60s 缓存 | dev-plan §7.5 同意 MVP REST，WebSocket 留给 Phase 1.5 |
| K2 | Keeper 私钥 | 本期 `.env`，Sprint 4 切 KMS | 主网部署但仅小额灰度，妥协可接受 |
| K3 | 多实例并发 | 单 worker 进程，advisory lock 兜底 | web 已拆出，worker 单跑足够；多 worker 收益不抵复杂度 |
| K3b | web / worker 进程拆分 | 同 image 不同 command | 重启隔离 + 水平扩容 web；改回合并只需改 docker-compose |
| K4 | 推送通道 | chrome.notifications + 轮询 | Web Push 需要 VAPID + service URL，Phase 1.5 再切 |
| K5 | 状态机新增 `liquidating` | 是 | 防止 keeper tick 内发完交易后、事件未到的窗口被另一个 tick 重复触发 |
| K6 | Indexer 起点 | 第一次启动从 `vault_deploy_block` 起；之后从 `MAX(lending_events.block_number)` | 简单可恢复，不依赖独立 cursor 表 |
| K7 | Gas 替换策略 | 60s 未确认 → 同 nonce tip×2，最多 3 次 | dev-plan §7.4 |
| K8 | Alert 阈值常量位置 | `services/lending/config.py` 集中管理 | 已有该文件，避免散落 |
| K9 | `liquidate` 接口签名 | `(uint256 loanId, string reason, uint256 currentPriceE6)` 价格精度 e6 | 走可信 keeper 路线，链下传价；reason ∈ {`ltv_breach`, `kickoff_due`} |
| K10 | 链上 transport | `web3.py >= 7.0` AsyncWeb3 | keeper 要发交易 + EIP-1559 gas + nonce 替换；event_decoder 现有 httpx 模式只读够用 |
| K11 | Day 5 验收 | mock vault 集成测试，主网灰度推迟 | 合约 stub 必须先真实化；UUPS proxy 准备好后才能上主网 |
| K12 | 合约升级治理 | UUPS + 24h timelock + 2/3 multisig admin | upgrade 权限本身是单点，必须 timelock 给社区时间撤销 |

---

## 5. 失败模式 & 兜底

| 故障 | 影响 | 兜底 |
|---|---|---|
| Keeper 私钥泄露 | 资金被盗 | 合约 `keeper` 角色只能 `liquidate`，无 `withdrawLP` 权限；Sprint 4 切 KMS |
| Polygon RPC 限流 | indexer 滞后、keeper 失明 | 已用 publicnode（见 memory `feedback_metamask_rate_limit`）；后端可加备源 alchemy |
| Gamma API 宕机 | 没价格 → 漏清算 | `price_watcher` 返回 None 时跳过该笔，记 `keeper.price_unavailable` 指标，连续 5 分钟告警 |
| 用户预警通知未读 → 仍被清算 | 用户体验差但合规 | ToS 已写明用户负责风控；Profile 内借贷 tab 也常驻显示 LTV 条 |
| `liquidating` 状态卡死（交易丢失） | loan 永远不被再触发 | 单独 cron：每 10 分钟扫 `status='liquidating' AND updated_at < now-15min`，回滚到 `active` |
| worker 容器 OOM / panic | 清算停摆 | `restart: unless-stopped` 自动拉起；advisory lock 在连接断开后由 PG 自动释放（秒级），新进程能立刻接管 |
| web 容器误装了 worker 入口 | 双跑发重复清算 | advisory lock 兜底：第二个进程秒退并 Sentry 告警 |
| 事件解码字段变更 | indexer 抛错 | 单事件 try/except + sentry，不阻塞整轮；ABI 改动手动迁移 |

---

## 6. 监控指标（埋点，Grafana 留给 Sprint 4）

```
keeper.tick_duration_ms
keeper.liquidations_triggered{reason="ltv_80"|"kickoff_2h"}
keeper.liquidation_failures
keeper.price_unavailable
event_indexer.lag_blocks
event_indexer.events_processed{type=…}
alert_engine.alerts_emitted{type=…}
alert_engine.tick_duration_ms
```

打到 stdout 即可（结构化 log），Sprint 4 接 Grafana。

---

## 7. Done Gate

按 dev-plan §9 Sprint 3 后端口径验收（**主网灰度推迟到合约 hand-off 期**）：

- [ ] worker 进程独立启动 / SIGTERM 优雅关停干净（3 个 loop 全部退出）
- [ ] 误启第二个 worker 立刻退出（advisory lock 生效）
- [ ] **mock vault 集成测试**：LTV 路径 + kickoff 路径各 1 笔走通 keeper → indexer → status = `liquidated_*` → SW 通知
- [ ] 集成测试套件 100% 通过
- [ ] Service Worker 收到 4 类预警通知至少各 1 次
- [ ] Sentry 收到刻意制造的异常并触发

合约期 hand-off 验收（不在本期，合约 ready 后做）：
- [ ] Polygon 主网手工触发：LTV / kickoff 各 1 笔成功清算，事件 / DB / 残值三方一致
- [ ] 合约工程师并行交付 “100 笔模拟清算零失败”
- [ ] 内部 3–5 人走通 “借 → 触发清算 → 收残值”

---

## 8. 风险登记

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| KR1 | Polygon 主网真实清算前缺 KMS | 🟡 | 本期 .env 私钥仅放灰度小额；Sprint 4 切 KMS |
| KR2 | Gamma API 价格滞后/异常 | 🟡 | 60s TTL + 上下限 sanity check（合约层已做）；记指标 |
| KR3 | 清算时 vault USDC 余额不足覆盖残值 | 🟡 | LP 池监控 < 总活跃借款 × 1.5 时禁用新借款（lending router 加守门） |
| KR4 | 用户在 kickoff-2h 前 1 分钟还款，撞上 keeper tick | 🟢 | 合约 `liquidate` 在 loan 已 `repaid` 时 revert；keeper 收 revert 回滚状态 |
| KR5 | Service Worker alarm 在用户关浏览器时不触发 | 🟢 | 已知限制；预警仍存在 DB，下次开浏览器补推 |

---

## 9. 后续

### 合约期 hand-off（紧接本期，主网上线前）

- 合约工程师 agent 完工 → 看 `contracts/REPORT.md`
- ABI 对齐：keeper 端 `vault_client.py` 的接口与合约真实签名 cross-check
- 部署新 UUPS proxy → 用户手动切 `backend/.env` 的 `KPAX_VAULT_ADDRESS` → 重启 worker 容器
- 旧 vault `0xB4357796...` wind down（如有活跃 loan 先 repay/liquidate）
- 主网灰度演练（按 §3 Day 5 原计划：admin 开 $1 loan + mock 价格触发）

### 远期（不在本计划范围）

- Sprint 4 审计 kickoff（合约工程师为主）
- KMS / HSM 切换（运维）
- WebSocket 价格源（Phase 1.5）
- Grafana 看板 + PagerDuty 接线（Sprint 4 运维）
