from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    app_name: str = "KPAX Ball API"
    debug: bool = False

    # Database
    database_url: str = "sqlite:///./kpax_ball.db"

    # AI — 智谱 GLM，通过 OpenAI 兼容模式调用
    zhipuai_api_key: str = ""
    zhipuai_api_base: str = "https://open.bigmodel.cn/api/paas/v4"
    default_ai_model: str = "openai/glm-5"
    quick_preview_model: str = "openai/glm-4-plus"

    # Polymarket
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com"
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_data_url: str = "https://data-api.polymarket.com"

    # Polygon RPC for Lending proxy detection (`eth_getCode`).
    # Default is a public node; production should use a paid RPC (Alchemy/QuickNode).
    polygon_rpc_url: str = "https://polygon-bor-rpc.publicnode.com"

    # Deployed LendingVault on Polygon mainnet. Empty string = use the
    # placeholder defined in services/lending/config.py.
    kpax_vault_address: str = ""

    # ---------- Lending worker (keeper / event indexer / alert engine) ----------
    # Block at which LendingVault was deployed; indexer rewinds here on a fresh
    # database. After first run, indexer resumes from MAX(lending_events.block_number).
    lending_vault_deploy_block: int = 0
    # Polygon mainnet (matches polygon_rpc_url default).
    lending_chain_id: int = 137
    # Hex-encoded private key (no 0x prefix required) for the keeper EOA used to
    # sign liquidate() transactions. Empty string disables liquidation (mock-only).
    keeper_private_key: str = ""
    # Admin EOA private key — signs `setPaused`, `emergencyWithdrawERC20`,
    # `depositLP`, `setTreasury`, etc. Distinct from the keeper key. Only
    # required for one-shot ops scripts under `backend/scripts/`; the keeper
    # / web app must NEVER use it.
    vault_admin_private_key: str = ""
    # Tick intervals (seconds). Match plan §3.
    keeper_interval_seconds: int = 30
    alert_engine_interval_seconds: int = 60
    event_indexer_interval_seconds: int = 5
    # EIP-1559 gas — see plan §3 Day 2. Priority tip in gwei applied to every
    # keeper tx; replacement bumps tip ×2 each retry up to keeper_gas_max_retries.
    keeper_priority_fee_gwei: int = 30
    keeper_gas_max_retries: int = 3
    keeper_replacement_after_seconds: int = 60
    # Postgres advisory lock key (decimal). Hex of "KPAXKEEP" → see plan §2.2.
    worker_advisory_lock_key: int = 0x4B504158_4B454550

    # Sentry — leave empty to disable. Enabled in both web and worker entrypoints.
    sentry_dsn: str = ""
    sentry_environment: str = "dev"

    # ---------- Polymarket CLOB (Sprint 4: keeper sells CTF) ----------
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_api_key: str = ""
    polymarket_api_secret: str = ""
    polymarket_api_passphrase: str = ""
    # Slippage tolerance (basis points) when keeper sells CTF on Polymarket.
    # min_proceeds = expected_proceeds × (1 - slippage_bps / 10_000)
    keeper_sell_slippage_bps: int = 200  # 2%

    # ---------- Polymarket CLOB V2 (deposit-wallet flow) ----------
    # PM CLOB V2 (cutover 2026-04-28) disallows EOA-direct trading; orders
    # must come from a deployed PM DepositWallet ("deposit wallet") owned by
    # the keeper EOA. The keeper signs orders + Batches; PM's relayer (which
    # holds the on-chain operator role) submits them via the
    # DepositWalletFactory. We therefore depend on PM's centralized relayer
    # for any wallet operation (move pUSD out, rollback CTF, etc.).
    keeper_proxy_address: str = ""
    # PM Relayer endpoint + API key tied to the keeper EOA (created from the
    # PM "Relayer API 密钥" account page). Without these the keeper can sign
    # batches but has no way to submit them on-chain.
    polymarket_relayer_url: str = "https://relayer-v2.polymarket.com"
    polymarket_relayer_api_key: str = ""
    # Polymarket V2 collateral (pUSD, 6-decimals, 1:1 backed by USDC.e).
    # All CLOB V2 trades settle in pUSD, so step 4 (proxy → vault) moves
    # pUSD, not USDC. The vault must accept pUSD (or unwrap pUSD → USDC.e
    # first via CollateralOfframp.unwrap()).
    polymarket_pusd_address: str = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
    # PM DepositWallet implementation factory. Same address PM uses to deploy
    # all V2 deposit wallets; the wallet's `factory()` getter must equal this.
    polymarket_deposit_wallet_factory: str = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"
    # PM DepositWallet implementation (the contract behind every V2 deposit
    # wallet's EIP-1967 proxy). Factory.predictWalletAddress(impl, owner) uses
    # this to derive the CREATE2 address.
    polymarket_deposit_wallet_impl: str = "0x58CA52ebe0DadfdF531Cde7062e76746de4Db1eB"
    # USDC.e (PoS bridge USDC, 6 decimals) — V3 vault underlying. Phase 1 §3.1
    # verified Onramp accepts ONLY this address (Native USDC `0x3c499…3359` is
    # rejected with `OnlyUnpaused()`). Pre-V3 vault held Native USDC.
    polymarket_usdce_address: str = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    # PM CollateralOnramp / Offramp — wrap (USDC.e → pUSD) and unwrap (pUSD →
    # USDC.e), 1:1 with no fee (only gas). Both have `wrap`/`unwrap` external
    # signatures `(address asset, address to, uint256 amount)`.
    polymarket_collateral_onramp: str = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
    polymarket_collateral_offramp: str = "0x2957922Eb93258b93368531d39fAcCA3B4dC5854"

    # API-Football (api-sports.io, real match data)
    api_football_key: str = ""

    # Zep (optional, for memory system in Phase 2)
    zep_api_key: str = ""

    # Privy (embedded wallet + auth) — 占位，正式使用前替换成真实值
    privy_app_id: str = "PRIVY_APP_ID_PLACEHOLDER"
    privy_app_secret: str = "PRIVY_APP_SECRET_PLACEHOLDER"
    privy_verification_key: str = ""  # Privy JWT 公钥（PEM 格式），从 Privy dashboard 拿
    privy_jwks_url: str = "https://auth.privy.io/api/v1/apps/{app_id}/jwks.json"

    # KPAX 自签 session JWT
    kpax_jwt_secret: str = "CHANGE_ME_IN_PRODUCTION"
    kpax_jwt_ttl_seconds: int = 60 * 60 * 24 * 7  # 7 天

    # Privy auth 前端页面（外部窗口）URL
    privy_auth_page_url: str = "https://kpax.bout.network/auth"

    # CORS — 开发阶段允许所有来源，生产环境应限制为具体的扩展 ID
    cors_origins: list[str] = ["*"]

    # Admin 后台账号 — 用于 /admin 静态页 + /api/admin/* 接口。
    # 留空 → 登录接口直接 503。生产环境必须设置高强度密码。
    kpax_admin_username: str = ""
    kpax_admin_password: str = ""
    # Admin session token 有效期。复用 kpax_jwt_secret 签名，但带 kind="admin"
    # claim，与普通用户 JWT 严格区分。
    kpax_admin_token_ttl_seconds: int = 60 * 60 * 12  # 12 小时

    # Cache
    preview_cache_ttl: int = 3600  # 1 hour
    data_cache_ttl: int = 3600  # 1 hour

    model_config = {"env_file": ".env"}


settings = Settings()
