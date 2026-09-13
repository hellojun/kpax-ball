"""KPAX Lending 参数配置（PRD §8 + dev plan §4.2 对齐）。

合约常量 (APR_BPS, LIQUIDATION_PENALTY_BPS) 必须与 LendingVault.sol 保持一致。
修改这里之前请同步更新合约常量并重新审计。
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------- ToS ----------

CURRENT_TOS_VERSION = "v1.0"


# ---------- 合约 ----------

# LendingVault deployed address (Polygon mainnet). Until we deploy, this is a
# burn-style placeholder so the frontend can plumb the approval flow end-to-end
# without accidentally granting a real contract permission to pull collateral.
# Env override: KPAX_VAULT_ADDRESS.
LENDING_VAULT_ADDRESS = "0x000000000000000000000000000000000000dEaD"


# ---------- 利率 ----------

APR_BPS = 1200  # 12%
SECONDS_PER_YEAR = 31_536_000
LIQUIDATION_PENALTY_BPS = 200  # 2%


# ---------- LTV 分档 ----------


@dataclass(frozen=True)
class LeagueTierConfig:
    tier: int
    label: str
    max_ltv: float
    warning_ltv: float
    liquidation_ltv: float
    min_market_depth_usd: int


LEAGUE_TIERS: dict[int, LeagueTierConfig] = {
    1: LeagueTierConfig(
        tier=1,
        label="Top (EPL / UCL / World Cup)",
        max_ltv=0.60,
        warning_ltv=0.70,
        liquidation_ltv=0.80,
        min_market_depth_usd=1_000_000,
    ),
    2: LeagueTierConfig(
        tier=2,
        label="Mid (La Liga / Serie A / Europa / cups)",
        max_ltv=0.50,
        warning_ltv=0.65,
        liquidation_ltv=0.75,
        min_market_depth_usd=200_000,
    ),
    3: LeagueTierConfig(
        tier=3,
        label="Low (Championship / Eredivisie / Primeira)",
        max_ltv=0.40,
        warning_ltv=0.55,
        liquidation_ltv=0.65,
        min_market_depth_usd=50_000,
    ),
}


# ---------- 时间窗口 ----------

# 比赛开赛前多少小时自动强平（PRD §8.5）
KICKOFF_BUFFER_HOURS = 2

# 比赛开赛前多少小时发预警
KICKOFF_WARN_HOURS = 4

# 最早可开仓时间：距开赛至少多少小时
MIN_HOURS_BEFORE_KICKOFF_TO_BORROW = 24


# ---------- 借款额度限制（Alpha / Beta 灰度）----------

# 单用户借款上限（USD），随灰度阶段调整
MAX_BORROW_PER_USER_USD = 500

# 全局 TVL 上限（USD）
MAX_TOTAL_TVL_USD = 50_000


def config_payload() -> dict:
    """供 /api/lending/config 返回的 JSON 结构。"""
    from app.config import settings

    return {
        "tos_version": CURRENT_TOS_VERSION,
        "vault_address": settings.kpax_vault_address or LENDING_VAULT_ADDRESS,
        "apr_bps": APR_BPS,
        "liquidation_penalty_bps": LIQUIDATION_PENALTY_BPS,
        "kickoff_buffer_hours": KICKOFF_BUFFER_HOURS,
        "kickoff_warn_hours": KICKOFF_WARN_HOURS,
        "min_hours_before_kickoff_to_borrow": MIN_HOURS_BEFORE_KICKOFF_TO_BORROW,
        "max_borrow_per_user_usd": MAX_BORROW_PER_USER_USD,
        "max_total_tvl_usd": MAX_TOTAL_TVL_USD,
        "tiers": [
            {
                "tier": t.tier,
                "label": t.label,
                "max_ltv": t.max_ltv,
                "warning_ltv": t.warning_ltv,
                "liquidation_ltv": t.liquidation_ltv,
                "min_market_depth_usd": t.min_market_depth_usd,
            }
            for t in LEAGUE_TIERS.values()
        ],
    }
