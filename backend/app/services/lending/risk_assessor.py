"""Heuristic LTV recommendation for KPAX Lending.

Sprint 2 keeps this rules-based — fast, deterministic, no LLM dependency.
A future iteration can replace `assess_risk` with an LLM call (see
`quick_preview.py` for the pattern) once we have ground-truth liquidation
data to evaluate against.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.services.lending.config import LEAGUE_TIERS


@dataclass
class RiskAssessment:
    asset: str
    recommended_borrow_usd: float
    recommended_ltv: float
    max_borrow_usd: float
    max_ltv: float
    league_tier: int
    risk_score: int  # 1 (very low) – 10 (very high)
    liquidation_probability_estimate: float
    risk_reasoning: str
    key_risks: list[dict[str, str]]


def _hours_to_kickoff(kickoff: datetime | None) -> float:
    if kickoff is None:
        return 7 * 24.0  # neutral default
    delta = kickoff - datetime.utcnow()
    return max(0.0, delta.total_seconds() / 3600.0)


def assess_risk(
    *,
    asset: str,
    league_tier: int,
    value_usd: float,
    current_price: float,
    kickoff_at: datetime | None,
    market_title: str | None = None,
) -> RiskAssessment:
    """Compute a conservative recommended LTV based on league tier, time-to-
    kickoff, position value, and how confident the market is (price near 0/1
    is "more decided" → less price volatility room → safer)."""
    tier_cfg = LEAGUE_TIERS[league_tier]
    max_ltv = tier_cfg.max_ltv
    max_borrow_usd = round(value_usd * max_ltv, 2)

    # Start at 85% of the tier ceiling — leaves room before warning/liquidation.
    ltv = max_ltv * 0.85

    # Time-to-kickoff adjustment: closer to kickoff = less time to react to
    # adverse moves. Compress LTV for matches starting within 48 hours.
    hours_to_kickoff = _hours_to_kickoff(kickoff_at)
    time_factor = 1.0
    if hours_to_kickoff < 48:
        # Linear ramp: 24h → 0.85, 48h → 1.0.
        time_factor = max(0.85, hours_to_kickoff / 48.0 + 0.5)
    ltv *= time_factor

    # Price-confidence adjustment: prices very close to 0 or 1 imply the
    # market has high conviction, so less room to move further (against the
    # holder's direction). For the holder of a $0.80 No share, the upside
    # is bounded by $1.00 - $0.80 = $0.20 = 25% of value, so we soften LTV.
    edge_to_resolution = min(current_price, 1 - current_price) if 0 < current_price < 1 else 0.5
    # If the position is near 0/1, edge is small → reduce LTV.
    if edge_to_resolution < 0.15:
        ltv *= 0.85

    ltv = round(min(ltv, max_ltv - 0.02), 2)  # always leave 2pp under hard cap
    recommended_borrow_usd = round(value_usd * ltv, 2)

    # Risk score: combine tier + time + edge into a 1–10 scale.
    score = 2  # tier 1 baseline
    if league_tier == 2:
        score += 2
    elif league_tier == 3:
        score += 4
    if hours_to_kickoff < 48:
        score += 1
    if hours_to_kickoff < 24:
        score += 1
    if edge_to_resolution < 0.15:
        score += 1
    score = max(1, min(10, score))

    # Rough liquidation probability — this is a heuristic, not a real model.
    base_p = {1: 0.04, 2: 0.07, 3: 0.12}[league_tier]
    if hours_to_kickoff < 24:
        base_p *= 1.4
    if edge_to_resolution < 0.15:
        base_p *= 1.3
    liq_prob = round(min(0.5, base_p), 3)

    risks = _build_risk_factors(
        league_tier=league_tier,
        hours_to_kickoff=hours_to_kickoff,
        edge_to_resolution=edge_to_resolution,
        market_title=market_title,
    )
    reasoning = _build_reasoning(
        league_tier=league_tier,
        hours_to_kickoff=hours_to_kickoff,
        ltv=ltv,
        max_ltv=max_ltv,
    )

    return RiskAssessment(
        asset=asset,
        recommended_borrow_usd=recommended_borrow_usd,
        recommended_ltv=ltv,
        max_borrow_usd=max_borrow_usd,
        max_ltv=max_ltv,
        league_tier=league_tier,
        risk_score=score,
        liquidation_probability_estimate=liq_prob,
        risk_reasoning=reasoning,
        key_risks=risks,
    )


def _build_reasoning(
    *,
    league_tier: int,
    hours_to_kickoff: float,
    ltv: float,
    max_ltv: float,
) -> str:
    tier_label = {1: "顶级联赛 (英超 / 欧冠 / 世界杯)", 2: "次级联赛", 3: "小众联赛"}[league_tier]
    days = hours_to_kickoff / 24
    return (
        f"{tier_label}, 距开赛约 {days:.1f} 天 → 推荐 {int(ltv * 100)}% LTV"
        f"（联赛上限 {int(max_ltv * 100)}%）。剩余的 LTV 缓冲用于吸收赛前价格波动，"
        f"避免触发 80% 强平阈值。"
    )


def _build_risk_factors(
    *,
    league_tier: int,
    hours_to_kickoff: float,
    edge_to_resolution: float,
    market_title: str | None,
) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []
    if hours_to_kickoff < 24:
        risks.append({
            "factor": "距开赛 <24 小时",
            "impact": "in-play 风险高；KPAX 在开赛前 2 小时强制平仓",
        })
    elif hours_to_kickoff < 72:
        risks.append({
            "factor": f"距开赛仅 {hours_to_kickoff:.0f} 小时",
            "impact": "阵容/伤停消息可能在赛前 24 小时大幅影响赔率",
        })
    if league_tier >= 3:
        risks.append({
            "factor": "小众联赛",
            "impact": "市场深度小，价格容易被单笔交易推动",
        })
    if 0.4 < edge_to_resolution < 0.6:
        risks.append({
            "factor": "市场不确定性高（接近 50/50）",
            "impact": "比赛进程中价格可能大幅波动",
        })
    if not risks:
        risks.append({
            "factor": "无显著风险因子",
            "impact": "条件相对友好",
        })
    return risks


def to_payload(r: RiskAssessment) -> dict[str, Any]:
    return {
        "asset": r.asset,
        "recommended_borrow_usd": r.recommended_borrow_usd,
        "recommended_ltv": r.recommended_ltv,
        "max_borrow_usd": r.max_borrow_usd,
        "max_ltv": r.max_ltv,
        "league_tier": r.league_tier,
        "risk_score": r.risk_score,
        "liquidation_probability_estimate": r.liquidation_probability_estimate,
        "risk_reasoning": r.risk_reasoning,
        "key_risks": r.key_risks,
    }
