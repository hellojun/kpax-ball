"""联赛名称 → tier 映射。

MVP 使用硬编码白名单；Phase 2 可改为按市场深度动态判定。
"""

from __future__ import annotations

# 关键词命中即匹配（case-insensitive）。顺序：先高档优先。
_TIER_KEYWORDS: list[tuple[int, tuple[str, ...]]] = [
    (
        1,
        (
            "premier league",
            "english premier",
            "epl",
            "champions league",
            "ucl",
            "world cup",
        ),
    ),
    (
        2,
        (
            "la liga",
            "bundesliga",
            "serie a",
            "ligue 1",
            "europa league",
            "uel",
            "europa conference",
            "fa cup",
            "copa del rey",
            "coppa italia",
            "dfb pokal",
        ),
    ),
    (
        3,
        (
            "championship",
            "eredivisie",
            "primeira liga",
            "scottish premiership",
            "super lig",
        ),
    ),
]


def classify_league_tier(competition: str | None) -> int | None:
    """Return 1/2/3 for supported leagues, or None if unsupported."""
    if not competition:
        return None
    c = competition.lower()
    for tier, keywords in _TIER_KEYWORDS:
        if any(k in c for k in keywords):
            return tier
    return None
