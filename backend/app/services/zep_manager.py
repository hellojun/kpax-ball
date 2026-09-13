"""ZEP 知识图谱管理 — 足球分析记忆系统。

实体类型：
- 球队（Team）：近况、风格、关键球员
- 赛事（Competition）：联赛特点、积分
- 分析记录（Analysis）：KPAX 预测 + 实际结果
- 洞察（Insight）：跨比赛的规律性发现

关系：
- Team --参加--> Competition
- Analysis --关于--> Team (home/away)
- Analysis --属于--> Competition
- Insight --来源--> Analysis
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.config import settings

logger = logging.getLogger(__name__)

GRAPH_ID = "kpax-ball"
SYSTEM_USER_ID = "kpax-system"

_client = None


def _zep_available() -> bool:
    return bool(settings.zep_api_key)


def get_zep_client():
    """获取 ZEP 客户端（单例）。"""
    global _client
    if _client is None:
        if not settings.zep_api_key:
            raise RuntimeError("ZEP_API_KEY not configured")
        from zep_cloud.client import Zep
        _client = Zep(api_key=settings.zep_api_key)
    return _client


def _ensure_user(user_id: str = SYSTEM_USER_ID) -> None:
    """确保用户在 ZEP 中存在。"""
    try:
        client = get_zep_client()
        client.user.add(user_id=user_id)
    except Exception:
        pass  # 已存在则忽略


# ==================== 写入 ====================

def push_analysis_result(
    home_team: str,
    away_team: str,
    competition: str,
    kpax_odds: dict,
    market_odds: dict,
    summary: str,
    confidence: str,
    slug: str,
) -> None:
    """将分析结果推送到 ZEP 知识图谱。"""
    if not _zep_available():
        return
    try:
        _ensure_user()
        client = get_zep_client()

        deviation = {}
        for key in ("home", "draw", "away"):
            k = kpax_odds.get(key, 0)
            m = market_odds.get(key, 0)
            if k or m:
                deviation[key] = round(k - m, 4)

        content = (
            f"足球分析记录 — {home_team} vs {away_team} ({competition})\n"
            f"时间: {datetime.utcnow().strftime('%Y-%m-%d')}\n"
            f"Polymarket 盘口: {slug}\n"
            f"市场赔率: 主{market_odds.get('home', 0):.0%} 平{market_odds.get('draw', 0):.0%} 客{market_odds.get('away', 0):.0%}\n"
            f"KPAX 预测: 主{kpax_odds.get('home', 0):.0%} 平{kpax_odds.get('draw', 0):.0%} 客{kpax_odds.get('away', 0):.0%}\n"
            f"置信度: {confidence}\n"
            f"分析摘要: {summary}\n"
            f"偏差: {', '.join(f'{k}={v:+.1%}' for k, v in deviation.items() if v)}"
        )

        client.graph.add(
            data=content,
            type="text",
            user_id=SYSTEM_USER_ID,

        )
        logger.info("Pushed analysis to ZEP: %s vs %s", home_team, away_team)
    except Exception as exc:
        logger.warning("Failed to push analysis to ZEP: %s", exc)


def push_debate_summary(
    home_team: str,
    away_team: str,
    competition: str,
    consensus: str,
    disagreements: str,
    key_variables: str,
) -> None:
    """将专家辩论总结推送到 ZEP。"""
    if not _zep_available():
        return
    try:
        _ensure_user()
        client = get_zep_client()

        content = (
            f"专家辩论总结 — {home_team} vs {away_team} ({competition})\n"
            f"时间: {datetime.utcnow().strftime('%Y-%m-%d')}\n"
            f"共识: {consensus}\n"
            f"分歧: {disagreements}\n"
            f"关键变量: {key_variables}"
        )

        client.graph.add(
            data=content,
            type="text",
            user_id=SYSTEM_USER_ID,

        )
        logger.info("Pushed debate summary to ZEP: %s vs %s", home_team, away_team)
    except Exception as exc:
        logger.warning("Failed to push debate summary to ZEP: %s", exc)


def push_verification_result(
    home_team: str,
    away_team: str,
    competition: str,
    predicted_outcome: str,
    actual_outcome: str,
    correct: bool,
    brier_score: float,
) -> None:
    """将赛后验证结果推送到 ZEP，用于记忆反馈。"""
    if not _zep_available():
        return
    try:
        _ensure_user()
        client = get_zep_client()

        content = (
            f"赛后验证 — {home_team} vs {away_team} ({competition})\n"
            f"KPAX 预测: {predicted_outcome}, 实际结果: {actual_outcome}\n"
            f"预测{'正确' if correct else '错误'}, Brier Score: {brier_score:.4f}\n"
            f"{'该分析模式可信' if correct else '该分析模式需要修正'}"
        )

        client.graph.add(
            data=content,
            type="text",
            user_id=SYSTEM_USER_ID,

        )
    except Exception as exc:
        logger.warning("Failed to push verification to ZEP: %s", exc)


# ==================== 读取 ====================

def search_knowledge(query: str, limit: int = 5) -> list[dict]:
    """搜索知识图谱，返回相关知识条目。"""
    if not _zep_available():
        return []
    try:
        client = get_zep_client()
        _ensure_user()
        results = client.graph.search(
            query=query,
            user_id=SYSTEM_USER_ID,

            limit=limit,
        )
        return [
            {"fact": edge.fact, "score": getattr(edge, "score", 0)}
            for edge in (results.edges or [])
            if edge.fact
        ]
    except Exception as exc:
        logger.warning("ZEP search failed: %s", exc)
        return []


def retrieve_context(query: str, limit: int = 5) -> str:
    """检索格式化的上下文文本，可直接注入 LLM prompt。"""
    results = search_knowledge(query, limit)
    if not results:
        return ""
    lines = [f"- {r['fact']}" for r in results]
    return "\n".join(lines)


def get_match_history(home_team: str, away_team: str, limit: int = 5) -> str:
    """获取两队的历史分析记忆。"""
    query = f"{home_team} vs {away_team} 分析 预测"
    return retrieve_context(query, limit)


def get_team_insights(team: str, limit: int = 5) -> str:
    """获取某支球队的累积洞察。"""
    query = f"{team} 分析 表现 趋势"
    return retrieve_context(query, limit)


# ==================== 图谱数据（用于可视化） ====================

KNOWN_TEAMS = [
    "Man City", "Arsenal", "Liverpool", "Chelsea", "Tottenham",
    "Man United", "Newcastle", "Brighton", "Aston Villa", "West Ham",
    "Crystal Palace", "Bournemouth", "Fulham", "Wolves", "Everton",
    "Brentford", "Nottingham Forest", "Leicester City", "Southampton",
    "Ipswich Town",
]

KNOWN_COMPETITIONS = ["Premier League", "Champions League", "World Cup", "Europa League"]


def get_graph_data() -> dict:
    """获取图谱数据用于前端可视化。

    从 ZEP facts 中提取已知球队和赛事实体，构建关系图。
    """
    if not _zep_available():
        return {"nodes": [], "edges": []}

    try:
        client = get_zep_client()
        _ensure_user()

        # 多维度搜索收集 facts
        all_facts: list[str] = []
        for query in ["football analysis Premier League", "KPAX prediction match", "team win probability"]:
            results = client.graph.search(
                query=query,
                user_id=SYSTEM_USER_ID,
                limit=20,
            )
            for edge in (results.edges or []):
                if edge.fact and edge.fact not in all_facts:
                    all_facts.append(edge.fact)

        nodes: dict[str, dict] = {}
        edges_set: set[tuple[str, str, str]] = set()

        for fact in all_facts:
            fact_lower = fact.lower()

            # 提取球队
            found_teams: list[str] = []
            for team in KNOWN_TEAMS:
                if team.lower() in fact_lower:
                    found_teams.append(team)
                    if team not in nodes:
                        nodes[team] = {"id": team, "type": "team", "label": team}

            # 提取赛事
            for comp in KNOWN_COMPETITIONS:
                if comp.lower() in fact_lower:
                    if comp not in nodes:
                        nodes[comp] = {"id": comp, "type": "competition", "label": comp}
                    for team in found_teams:
                        edges_set.add((team, comp, "参加"))

            # 两队对阵 → 分析节点
            if len(found_teams) >= 2:
                t1, t2 = found_teams[0], found_teams[1]
                match_id = f"{t1}_vs_{t2}"
                if match_id not in nodes:
                    nodes[match_id] = {
                        "id": match_id,
                        "type": "analysis",
                        "label": f"{t1} vs {t2}",
                        "fact": fact[:120],
                    }
                edges_set.add((t1, match_id, "主场"))
                edges_set.add((t2, match_id, "客场"))

        edges = [{"source": s, "target": t, "label": l} for s, t, l in edges_set]
        return {"nodes": list(nodes.values()), "edges": edges}

    except Exception as exc:
        logger.warning("Failed to get graph data: %s", exc)
        return {"nodes": [], "edges": []}
