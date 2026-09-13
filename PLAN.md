# KPAX Ball — Development Plan

> Polymarket 浏览器插件：足球盘口 AI 分析工具
> 产品文档: [kpax-ball.md](../Agentxlab/kpax-ball.md)
> 竞品分析: [kpax-ball-comp.md](../Agentxlab/kpax-ball-comp.md)

---

## 项目结构

```
kpax-ball/
├── PLAN.md
├── CLAUDE.md
├── README.md
├── docker-compose.yml
│
├── extension/                          # Chrome Extension (Manifest V3)
│   ├── manifest.json
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── public/
│   │   └── icons/                      # Extension icons (16/48/128)
│   └── src/
│       ├── content/                    # Content Script — 注入 Polymarket 页面
│       │   ├── index.ts                # 入口：检测页面、提取盘口数据
│       │   ├── detector.ts             # Polymarket 页面/盘口识别
│       │   ├── extractor.ts            # DOM + API 数据提取
│       │   └── styles.css              # 注入的最小样式（KPAX 图标）
│       ├── sidepanel/                  # Side Panel — 分析结果 UI
│       │   ├── index.html
│       │   ├── main.tsx
│       │   ├── App.tsx
│       │   ├── components/
│       │   │   ├── QuickPreview.tsx     # 快速预览卡片
│       │   │   ├── FullReport.tsx       # 完整分析报告
│       │   │   ├── ExpertDebate.tsx     # 专家辩论过程展示
│       │   │   ├── ConfidenceBadge.tsx  # 置信度标签
│       │   │   ├── OddsComparison.tsx   # 市场赔率 vs KPAX 分析
│       │   │   └── FollowUp.tsx        # 追问输入
│       │   └── styles/
│       │       └── index.css
│       ├── background/                 # Service Worker
│       │   └── index.ts                # API 通信、缓存管理、消息路由
│       └── shared/                     # 共享代码
│           ├── types.ts                # TypeScript 类型定义
│           ├── api.ts                  # 后端 API 客户端
│           ├── polymarket-api.ts       # Polymarket Gamma/CLOB API 客户端
│           └── constants.ts
│
├── backend/                            # FastAPI 后端
│   ├── app/
│   │   ├── main.py                     # FastAPI 入口
│   │   ├── config.py                   # pydantic-settings 配置
│   │   ├── db.py                       # SQLAlchemy 数据库
│   │   │
│   │   ├── routers/
│   │   │   ├── analysis.py             # POST /api/analysis/preview, /api/analysis/deep
│   │   │   ├── market.py               # GET /api/market/detect, /api/market/data
│   │   │   └── verification.py         # POST /api/verification/check, GET /api/verification/stats
│   │   │
│   │   ├── services/
│   │   │   ├── market_parser.py        # Polymarket 盘口解析（Gamma API）
│   │   │   ├── football_data.py        # 足球数据聚合（FBref/Transfermarkt）
│   │   │   ├── quick_preview.py        # 快速预览生成（<2s）
│   │   │   ├── deep_analysis.py        # 深度分析编排（调用辩论引擎）
│   │   │   ├── report_generator.py     # 报告结构化输出
│   │   │   ├── post_match_verifier.py  # 赛后验证 + 记忆反馈
│   │   │   │
│   │   │   │── # ---- 从 Agentxlab 复用 ----
│   │   │   ├── debate_engine.py        # 多 Agent 辩论引擎
│   │   │   ├── ai_provider.py          # LiteLLM 多模型调度
│   │   │   ├── reverse_discovery.py    # 反向发现引擎
│   │   │   └── token_quota.py          # Token 配额系统
│   │   │
│   │   └── models/
│   │       ├── analysis.py             # Analysis, AnalysisResult
│   │       ├── market.py               # Market, MarketSnapshot
│   │       └── verification.py         # MatchResult, VerificationRecord
│   │
│   ├── migrations/                     # Alembic
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
│
└── scripts/
    ├── sync_football_data.py           # 足球数据定期同步
    └── run_verification.py             # 赛后批量验证脚本
```

---

## 技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| Chrome 插件 | TypeScript + React 19 + Tailwind CSS 4 + Vite | Manifest V3, Side Panel API |
| 后端 API | Python FastAPI + Pydantic v2 + SQLAlchemy 2.0 | 沿用 Agentxlab 约定 |
| 数据库 | SQLite（开发）/ PostgreSQL 16（生产） | Alembic 迁移 |
| AI 层 | LiteLLM（多模型调度） | 默认 deepseek/deepseek-chat |
| 数据源 | Polymarket Gamma API + CLOB API + FBref | 公开 API，无需认证（读取） |
| 部署 | Docker Compose | Nginx + FastAPI + PostgreSQL |

### Polymarket API 概要

| API | Base URL | 用途 | 认证 |
|-----|----------|------|------|
| Gamma API | `gamma-api.polymarket.com` | 市场发现、盘口详情、赔率 | 无需 |
| CLOB API | `clob.polymarket.com` | 订单簿深度、实时价格、历史价格 | 公开端点无需 |
| WebSocket | `ws-subscriptions-clob.polymarket.com/ws/market` | 实时价格推送 | 无需 |
| Data API | `data-api.polymarket.com` | 交易历史 | 无需 |

关键端点：
- `GET /markets?slug={slug}` — 按 slug 查盘口详情（赔率、成交量、到期时间）
- `GET /events?slug={slug}` — 按 slug 查事件
- `GET /book?token_id={id}` — 订单簿
- `GET /prices-history?market={conditionId}&interval=max&fidelity=60` — 价格历史
- URL 模式：`polymarket.com/event/{event-slug}?tid={market-id}`

---

## Phase 1：MVP（4-6 周）

> 目标：100 个安装用户中，周活跃率 > 30%

### Sprint 1（Week 1-2）：数据层 + 最小后端

#### 1.1 Polymarket 市场解析器

**文件**: `backend/app/services/market_parser.py`

- 调用 Gamma API 获取市场数据
- 从市场 question/slug 中识别足球比赛（正则 + LLM fallback）
- 提取结构化参数：
  ```python
  @dataclass
  class FootballMarket:
      home_team: str
      away_team: str
      competition: str          # "Premier League", "World Cup"
      market_type: str          # "match_winner", "over_under", "both_to_score"
      polymarket_odds: dict     # {"home": 0.45, "away": 0.35, "draw": 0.20}
      condition_id: str
      clob_token_ids: list[str]
      end_date: datetime
      volume: float
      slug: str
  ```
- 过滤逻辑：只处理英超 + 世界杯相关盘口

**验收标准**: 给定一个 Polymarket 足球盘口 slug，返回结构化的 FootballMarket 对象

#### 1.2 足球数据聚合层

**文件**: `backend/app/services/football_data.py`

- 数据源优先级：FBref（免费、数据全）> Transfermarkt（补充球员信息）
- 按比赛拉取：
  - 两队近 5 场比赛数据（胜/平/负、进球数、控球率）
  - 联赛排名和积分
  - 关键球员伤病/停赛状态
  - 历史交锋记录（近 5 次）
- 缓存策略：赛前 24 小时内的数据缓存 1 小时，更早的缓存 24 小时
- 输出统一的 `MatchContext` 数据结构

**验收标准**: 给定两支球队名和赛事名，返回完整的 MatchContext

#### 1.3 后端 API 骨架

**文件**: `backend/app/main.py`, `routers/`, `models/`

- FastAPI 应用初始化，CORS 配置（允许 Chrome 插件域）
- 数据库模型：Analysis, Market, MatchResult
- 路由：
  - `POST /api/analysis/preview` — 快速预览
  - `POST /api/analysis/deep` — 深度分析（SSE 流式返回）
  - `GET /api/market/detect?slug={slug}` — 检测盘口是否为足球
  - `GET /api/verification/stats` — 历史准确率统计
- Alembic 迁移初始化

**验收标准**: API 启动正常，Swagger 文档可访问，数据库迁移通过

---

### Sprint 2（Week 2-3）：分析引擎

#### 2.1 快速预览生成器

**文件**: `backend/app/services/quick_preview.py`

- 输入：FootballMarket + MatchContext
- 流程：
  1. 检查缓存（同一盘口 1 小时内有缓存直接返回）
  2. 构建 prompt：球队近况 + 赔率 + 关键伤病 → 一次 LLM 调用
  3. 输出结构化预览：
     ```python
     @dataclass
     class QuickPreview:
         summary: str              # 一句话判断
         kpax_odds: dict           # {"home": 0.38, "away": 0.34, "draw": 0.28}
         market_deviation: dict    # {"home": -0.07, "away": -0.01, "draw": +0.08}
         confidence: str           # "high" | "medium" | "low"
         confidence_reason: str
     ```
  4. 写入缓存
- 目标延迟：< 2 秒（单次 LLM 调用，用 deepseek-chat 或 haiku）

**验收标准**: 输入足球盘口数据，2 秒内返回结构化的 QuickPreview

#### 2.2 深度分析引擎

**文件**: `backend/app/services/deep_analysis.py`

- 输入：FootballMarket + MatchContext + 用户补充信息（可选）
- 流程：
  1. 从 Agentxlab 的 `reverse_discovery.py` 获取相关学科推荐
  2. 生成 4 个足球专家 Agent（复用 `debate_engine.py`）：
     - 战术网络分析师
     - 统计建模专家
     - 战术解读专家
     - 心理情境分析师
  3. 运行 2-3 轮辩论（SSE 流式输出）
  4. 调用报告生成器输出结构化报告
- 目标：1-3 分钟完成

**依赖**: 从 Agentxlab 复制并适配 `debate_engine.py`、`ai_provider.py`、`reverse_discovery.py`、`token_quota.py`

**适配改动**:
- `debate_engine.py`: 修改 Agent 生成逻辑，从通用学术专家改为足球领域专家；减少轮数（2-3 轮 vs 原来 6 轮）；修改 system prompt
- `ai_provider.py`: 基本不改，可能调整默认 temperature
- `reverse_discovery.py`: 可选使用，用于非标准问题的学科识别
- `token_quota.py`: 简化，MVP 阶段不做付费

**验收标准**: 输入足球盘口数据，通过 SSE 流式返回专家辩论过程，最终输出完整结构化报告

#### 2.3 报告生成器

**文件**: `backend/app/services/report_generator.py`

- 输入：辩论历史 + MatchContext + QuickPreview
- 输出结构化报告（JSON）：
  ```python
  @dataclass
  class FullReport:
      core_judgment: CoreJudgment       # 概率 + 置信度 + 偏差
      analysis_sections: list[Section]   # 五维分析
      key_variables: list[KeyVariable]   # 翻转条件
      expert_disagreements: list[Disagreement]
      historical_reference: str | None   # 历史分析准确率
  ```

**验收标准**: 给定辩论结果，输出完整的 FullReport JSON

---

### Sprint 3（Week 3-5）：Chrome 插件

#### 3.1 Content Script — 页面检测 + 数据提取

**文件**: `extension/src/content/`

- 页面检测：URL 匹配 `polymarket.com/event/*`
- 数据提取策略（优先级）：
  1. **Gamma API**（首选）：从 URL 解析 slug → 调用 `GET /events?slug={slug}` 获取完整数据
  2. **DOM 解析**（备选）：提取页面上的赔率、标题等（参考 PolymarketOddsConverter 的 DOM 选择器方案）
- MutationObserver 监听页面动态变化（Polymarket 是 React SPA）
- 提取完成后，通过 `chrome.runtime.sendMessage` 发送给 Service Worker
- 注入 KPAX 浮动图标（右下角，不遮挡页面内容）

**验收标准**: 打开任意 Polymarket 足球盘口页面，控制台打印出结构化盘口数据

#### 3.2 Service Worker — 消息路由 + 缓存

**文件**: `extension/src/background/index.ts`

- 接收 Content Script 的盘口数据
- 调用 KPAX 后端 API（preview / deep）
- 本地缓存管理（chrome.storage.local）：
  - 快速预览：缓存 1 小时
  - 深度分析：缓存到比赛开始
- 管理 Side Panel 的打开/关闭
- 处理用户认证 token（Phase 2）

**验收标准**: Content Script 发送盘口数据后，Service Worker 成功调用后端 API 并返回结果

#### 3.3 Side Panel — 分析展示 UI

**文件**: `extension/src/sidepanel/`

- 使用 Chrome Side Panel API（`chrome.sidePanel`）
- 两种状态：
  1. **折叠态（快速预览）**：一句话判断 + 概率偏差 + 置信度徽章 + "查看完整分析"按钮
  2. **展开态（完整报告）**：五维分析 + 专家分歧 + 关键变量 + 追问入口
- 深度分析 SSE 流式展示（显示专家辩论过程）
- 追问功能：用户输入 → 发送到后端 → 增量更新报告
- 响应式布局：Side Panel 宽度固定 ~400px

**UI 组件**:
- `QuickPreview.tsx` — 预览卡片：赔率对比条形图、一句话判断、置信度
- `FullReport.tsx` — 完整报告：分析摘要、关键变量、专家分歧
- `ExpertDebate.tsx` — 辩论过程：时间线展示、专家头像+发言
- `ConfidenceBadge.tsx` — 置信度标签：高（绿）/中（黄）/低（红）
- `OddsComparison.tsx` — 赔率对比：市场 vs KPAX，高亮偏差
- `FollowUp.tsx` — 追问输入框 + 补充信息入口

**验收标准**: 在 Polymarket 页面点击 KPAX 图标，Side Panel 弹出显示快速预览；点击深度分析后流式展示辩论过程和最终报告

#### 3.4 集成测试 + 打包

- 端到端测试：Polymarket 页面 → 检测盘口 → 快速预览 → 深度分析 → 报告展示
- Chrome Extension 打包（zip for dev，准备 Chrome Web Store 发布）
- 后端部署到可访问地址

**验收标准**: 完整流程跑通，插件可以在 Chrome 中加载使用

---

### Sprint 4（Week 5-6）：赛后验证 + 打磨

#### 4.1 赛后验证器

**文件**: `backend/app/services/post_match_verifier.py`

- 定时任务（每天 UTC 6:00 跑一次）：
  1. 查询所有已结束但未验证的比赛
  2. 从数据源获取比赛结果
  3. 与 KPAX 分析预测对比
  4. 计算准确率指标：
     - 二元准确率：预测的最高概率结果是否正确
     - Brier Score：概率校准度
     - 偏差方向准确率：标记为"高估/低估"的判断是否正确
  5. 记录到 VerificationRecord 表
- 在 Side Panel 展示历史准确率

**验收标准**: 赛后自动验证，准确率数据可在插件中查看

#### 4.2 用户体验打磨

- Content Script 的足球盘口识别准确率优化
- 加载状态、错误状态、空状态的 UI 处理
- 插件权限最小化（只申请必要权限）
- 免责声明文案
- Chrome Web Store 发布准备（描述、截图、隐私政策）

**验收标准**: 插件可提交 Chrome Web Store 审核

---

## Phase 2：数据飞轮 + 付费（Phase 1 完成后 4-6 周）

> 目标：付费转化率 > 5%

### 2.1 记忆系统（L1-L3）

- L1 持久记忆：用户关注的联赛/球队偏好
- L2 技能记忆：分析模板沉淀（如"强队客场被低估"模式）
- L3 会话搜索：历史分析检索（"上次分析这两队时说了什么"）
- 赛后验证结果自动修正模板权重

### 2.2 专业数据源接入

- StatsBomb API（高级数据：xG、传球网络）
- FBref 深度数据（球员级别统计）
- 预测市场交叉比价（Betfair, Kalshi）

### 2.3 付费功能

- Stripe 订阅接入（$5-15/月）
- 免费用户：每周 3 次深度分析
- 付费用户：无限深度分析 + 历史面板 + 邮件摘要

### 2.4 分析历史面板

- 用户可回看所有历史分析
- 按赛事/球队/时间筛选
- 个人准确率追踪

---

## Phase 3：扩场景 + 扩平台（Phase 2 完成后）

- 新盘口类型：NBA、NFL、政治选举
- 共享记忆（L4）上线
- Firefox / Safari 适配
- 独立站 / 移动端
- 每日邮件摘要（"本轮英超 KPAX 发现的最大偏差盘口"）
- Betfair / Kalshi 等其他预测市场支持

---

## 从 Agentxlab 复制的文件清单

以下文件从 `Agentxlab/projects/knowledge-graph/backend/app/services/` 复制到 `kpax-ball/backend/app/services/`，并做必要适配：

| 源文件 | 适配改动 |
|--------|---------|
| `debate_engine.py` | 修改 Agent 生成逻辑（足球专家而非学术专家）；减少辩论轮数；修改 system prompt |
| `ai_provider.py` | 基本不改；确认 LiteLLM 配置一致 |
| `reverse_discovery.py` | 可选使用；用于非标准足球问题的学科识别 |
| `token_quota.py` | 简化 plan_config（MVP 只有 free 计划） |

同时复用的配置模式：
- `config.py` — pydantic-settings 配置模式
- `db.py` — SQLAlchemy 2.0 + Alembic 模式
- Router / Service / Model 分层模式

---

## 关键风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Polymarket DOM 变更导致 Content Script 失效 | 高 | 中 | 优先用 Gamma API 而非 DOM 解析；DOM 做多套选择器容错 |
| 快速预览延迟 > 2s | 中 | 高 | 用最快的模型（haiku/deepseek）；激进缓存策略 |
| 足球盘口识别误判 | 中 | 中 | 先做白名单（英超/世界杯关键词），再逐步放开 |
| Chrome Web Store 审核不通过 | 低 | 高 | 权限最小化；不修改 Polymarket 页面内容；完善隐私政策 |
| Agentxlab 辩论引擎适配工作量超预期 | 中 | 中 | Sprint 2 预留缓冲；必要时先跳过辩论用单次 LLM 调用替代 |

---

## 验收里程碑

| 里程碑 | 时间 | 标准 |
|--------|------|------|
| M1: 后端 API 可调用 | Week 2 | 快速预览 + 深度分析 API 返回正确结果 |
| M2: 插件可安装使用 | Week 4 | 完整流程跑通：盘口检测 → 预览 → 深度分析 → 报告 |
| M3: MVP 发布 | Week 6 | Chrome Web Store 上架，赛后验证器运行，10 人内测 |
| M4: Phase 1 验证 | Week 8 | 100 安装用户，周活跃率 > 30% |
