"""专家 Agent 认知记忆 — 三层记忆架构。

每位足球专家 Agent 有独立的 ZEP 用户，积累三层认知：
- Facts: 具体事实（"曼城近5场主场全胜"）
- Arguments: 分析论点（"曼城的高控球率在强队面前效果打折"）
- Sparks: 洞察灵感（"水晶宫的低位防守反击是曼城的克星模式"）

辩论结束后，通过 LLM 蒸馏将新知识融入已有认知。
"""

from __future__ import annotations

import logging

from app.services.ai_provider import chat_completion
from app.services.zep_manager import _zep_available, get_zep_client, _ensure_user

logger = logging.getLogger(__name__)

LAYER_PREFIXES = {
    "facts": "[FACT]",
    "arguments": "[ARGUMENT]",
    "sparks": "[SPARK]",
}


def _agent_user_id(expert_role: str) -> str:
    """每位专家一个 ZEP 用户 ID。"""
    return f"kpax-expert-{expert_role}"


def retrieve_expert_cognition(expert_role: str, query: str, limit: int = 10) -> str:
    """检索专家的历史认知。"""
    if not _zep_available():
        return ""
    try:
        client = get_zep_client()
        user_id = _agent_user_id(expert_role)
        _ensure_user(user_id)

        results = client.graph.search(
            query=query,
            user_id=user_id,
            limit=limit,
        )
        facts = [edge.fact for edge in (results.edges or []) if edge.fact]
        return "\n".join(f"- {f}" for f in facts) if facts else ""
    except Exception as exc:
        logger.warning("Failed to retrieve cognition for %s: %s", expert_role, exc)
        return ""


def format_cognition_for_prompt(expert_role: str, match_context: str) -> str | None:
    """为专家的 system prompt 格式化历史认知。"""
    cognition = retrieve_expert_cognition(expert_role, match_context)
    if not cognition:
        return None

    return (
        "\n\n## 历史认知（来自你过去的分析积累）\n"
        "以下是你之前分析中积累的相关知识，可以用来增强你的论点，但不要简单重复：\n"
        f"{cognition}"
    )


async def distill_expert_cognition(
    expert_role: str,
    expert_name: str,
    debate_content: str,
    match_context: str,
) -> None:
    """辩论结束后，蒸馏专家的新认知并写入 ZEP。

    使用 LLM 对比已有认知和新辩论内容，提取值得记住的新知识。
    """
    if not _zep_available():
        return

    try:
        # 获取已有认知
        existing = retrieve_expert_cognition(expert_role, match_context)

        # 用 LLM 蒸馏新知识
        prompt = (
            f"你是足球分析专家 {expert_name}。\n"
            f"以下是你在一场辩论中的发言：\n{debate_content}\n\n"
            f"以下是你已有的认知：\n{existing or '（暂无）'}\n\n"
            "请提取这次辩论中值得记住的新知识，分三类：\n"
            "1. [FACT] 具体事实（数据、战绩、伤病等）\n"
            "2. [ARGUMENT] 分析论点（规律性判断）\n"
            "3. [SPARK] 洞察灵感（跨比赛的联系、新发现）\n\n"
            "每类 1-3 条，用换行分隔。只输出新的、已有认知中没有的内容。"
        )

        new_cognition = await chat_completion(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=4000,
        )

        if not new_cognition.strip():
            return

        # 写入 ZEP
        client = get_zep_client()
        user_id = _agent_user_id(expert_role)
        _ensure_user(user_id)

        client.graph.add(
            data=new_cognition,
            type="text",
            user_id=user_id,
        )
        logger.info("Distilled cognition for expert %s", expert_name)

    except Exception as exc:
        logger.warning("Cognition distillation failed for %s: %s", expert_name, exc)
