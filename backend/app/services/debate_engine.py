"""Football expert debate engine.

Adapted from Agentxlab's academic debate engine for football market analysis.
Orchestrates 4 football expert agents through 2-3 rounds of structured debate,
yielding SSE-compatible messages for real-time streaming.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from app.services.ai_provider import chat_completion

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3


@dataclass
class ExpertAgent:
    name: str
    role: str
    system_prompt: str
    sort_order: int
    is_moderator: bool = False


@dataclass
class DebateMessage:
    expert: str
    role: str
    content: str
    round_number: int


FOOTBALL_EXPERTS = [
    {
        "name": "战术网络分析师",
        "name_en": "Tactical Network Analyst",
        "role": "tactical_network",
        "desc": (
            "Analyze team tactical systems, formation matchups, and pressing patterns. "
            "Focus on how tactical structures create advantages or vulnerabilities. "
            "Use concepts like pressing intensity, build-up play patterns, width utilization, "
            "and defensive line height. Reference specific tactical trends from recent matches."
        ),
    },
    {
        "name": "统计建模专家",
        "name_en": "Statistical Modeler",
        "role": "statistical",
        "desc": (
            "Apply statistical models to assess match probabilities. "
            "Use concepts like expected goals (xG), expected points, Poisson distribution for "
            "goal scoring, Elo ratings, and regression models. "
            "Compare market odds to model-derived probabilities and identify value."
        ),
    },
    {
        "name": "战术解读专家",
        "name_en": "Tactical Interpreter",
        "role": "tactical_interpret",
        "desc": (
            "Interpret how specific tactical matchups and personnel decisions affect outcomes. "
            "Focus on key player battles, set piece threats, substitution patterns, and "
            "how managers historically adapt their systems against specific opponents."
        ),
    },
    {
        "name": "心理情境分析师",
        "name_en": "Psychological Context Analyst",
        "role": "psychological",
        "desc": (
            "Analyze psychological and contextual factors that affect performance. "
            "Consider team morale, fixture congestion, home/away dynamics, derby significance, "
            "pressure from league position, fan atmosphere, referee tendencies, and "
            "how recent results affect confidence."
        ),
    },
]

ROUND_OPENERS = {
    1: (
        "Round 1 — Opening Analysis. Present your assessment using bullet points:\n"
        "- Your key finding (1 sentence)\n"
        "- 2-3 supporting factors with specific data\n"
        "- Your preliminary probability estimate for each outcome\n"
        "- 1 question for other experts"
    ),
    2: (
        "Round 2 — Cross-examination. Respond to other experts:\n"
        "- Identify 1-2 points you agree with (and why)\n"
        "- Challenge 1-2 points you disagree with (cite evidence)\n"
        "- Refine your probability estimate based on the discussion"
    ),
    3: (
        "Round 3 — Final Assessment. Synthesize:\n"
        "- Your final probability estimate for each outcome\n"
        "- The single most important factor in this match\n"
        "- Key uncertainty: what could flip the result"
    ),
}


def _build_expert_system_prompt(
    expert: dict,
    match_info: str,
    polymarket_odds: dict,
    all_expert_names: list[str],
    language: str = "zh",
) -> str:
    """Build a contextual system prompt for a football expert agent."""
    odds_str = ", ".join(f"{k}: {v:.0%}" for k, v in polymarket_odds.items() if v)
    lang_rule = "用中文回复" if language == "zh" else "Respond in English"

    return (
        f"You are **{expert['name_en']}** ({expert['name']}), a football analysis expert "
        f"participating in a structured debate about a Polymarket football market.\n\n"
        f"## Your Expertise\n{expert['desc']}\n\n"
        f"## Match Context\n{match_info}\n\n"
        f"## Current Polymarket Odds\n{odds_str}\n\n"
        f"## Other Experts\n{', '.join(n for n in all_expert_names if n != expert['name_en'])}\n\n"
        f"## Output Rules\n"
        f"- Use **bullet points**, not long paragraphs\n"
        f"- Keep under 200 words per response\n"
        f"- Be specific: cite data, name players, reference matches\n"
        f"- Engage with other experts — agree, challenge, or build upon their points\n"
        f"- Probability estimates must be explicit numbers that sum to 1.0\n"
        f"- NEVER use language like 'buy', 'sell', 'bet'. Say 'analysis suggests...'\n"
        f"- **{lang_rule}**"
    )


MODERATOR_PROMPT = (
    "You are the Moderator of a football analysis debate.\n"
    "- Synthesize all expert perspectives into a clear summary\n"
    "- Highlight consensus and unresolved disagreements\n"
    "- Identify the key variables that could change the outcome\n"
    "- Present a balanced final probability estimate\n"
    "- Remain neutral — do not favor any expert's view\n\n"
    "Respond in English. Use bullet points. Be concise but insightful."
)


def generate_agents(
    match_info: str,
    polymarket_odds: dict,
    language: str = "zh",
) -> list[ExpertAgent]:
    """Generate the 4 football expert agents + moderator."""
    expert_names = [e["name_en"] for e in FOOTBALL_EXPERTS]
    agents: list[ExpertAgent] = []

    for i, expert in enumerate(FOOTBALL_EXPERTS):
        prompt = _build_expert_system_prompt(
            expert, match_info, polymarket_odds, expert_names, language=language,
        )
        # ZEP: 注入专家历史认知
        try:
            from app.services.agent_memory import format_cognition_for_prompt
            cognition = format_cognition_for_prompt(expert["role"], match_info[:200])
            if cognition:
                prompt += cognition
        except Exception:
            pass

        agents.append(ExpertAgent(
            name=expert["name_en"],
            role=expert["role"],
            system_prompt=prompt,
            sort_order=i,
        ))

    # Add moderator
    lang_rule = "用中文回复。使用要点列表格式。" if language == "zh" else "Respond in English. Use bullet points."
    mod_prompt = MODERATOR_PROMPT + f"\n\n{lang_rule}\n\nMatch context:\n{match_info}"
    agents.append(ExpertAgent(
        name="Moderator",
        role="moderator",
        system_prompt=mod_prompt,
        sort_order=len(FOOTBALL_EXPERTS),
        is_moderator=True,
    ))

    return agents


async def run_debate_stream(
    agents: list[ExpertAgent],
    num_rounds: int = 2,
):
    """Async generator: run debate rounds, yielding each message as it's produced.

    Round 1: 4 个专家并发（无依赖），按原顺序 yield
    Round 2+: 串行（需要看到前面的发言）
    """
    import asyncio

    num_rounds = min(num_rounds, MAX_ROUNDS)
    history: list[DebateMessage] = []
    experts = [a for a in agents if not a.is_moderator]
    moderator = next((a for a in agents if a.is_moderator), None)

    for round_num in range(1, num_rounds + 1):
        opener = ROUND_OPENERS.get(round_num, ROUND_OPENERS[3])

        if round_num == 1:
            # Round 1: 并发调用所有专家（互相无依赖）
            async def _call_expert(agent: ExpertAgent) -> DebateMessage:
                messages = [
                    {"role": "system", "content": agent.system_prompt},
                    {"role": "user", "content": opener},
                ]
                content = await chat_completion(
                    messages, temperature=0.7, max_tokens=800,
                )
                return DebateMessage(
                    expert=agent.name, role=agent.role,
                    content=content, round_number=round_num,
                )

            results = await asyncio.gather(*[_call_expert(a) for a in experts])
            for msg in results:
                history.append(msg)
                yield msg
        else:
            # Round 2+: 串行（需要看到前面的发言来回应）
            for agent in experts:
                messages = [{"role": "system", "content": agent.system_prompt}]
                for msg in history:
                    messages.append({
                        "role": "user",
                        "content": f"[{msg.expert}]: {msg.content}",
                    })
                messages.append({"role": "user", "content": opener})

                content = await chat_completion(
                    messages, temperature=0.7, max_tokens=800,
                )

                debate_msg = DebateMessage(
                    expert=agent.name, role=agent.role,
                    content=content, round_number=round_num,
                )
                history.append(debate_msg)
                yield debate_msg

        # Moderator summarizes after each round (except round 1)
        if moderator and round_num > 1:
            messages = [{"role": "system", "content": moderator.system_prompt}]
            for msg in history:
                messages.append({
                    "role": "user",
                    "content": f"[{msg.expert}]: {msg.content}",
                })
            messages.append({
                "role": "user",
                "content": (
                    f"Round {round_num} has concluded. Provide a brief synthesis:\n"
                    "- Where do experts agree?\n"
                    "- Where do they disagree?\n"
                    "- What's the emerging consensus probability?"
                ),
            })

            content = await chat_completion(
                messages, temperature=0.5, max_tokens=800,
            )

            debate_msg = DebateMessage(
                expert="Moderator", role="moderator",
                content=content, round_number=round_num,
            )
            history.append(debate_msg)
            yield debate_msg


async def generate_final_summary(
    agents: list[ExpertAgent],
    history: list[DebateMessage],
    polymarket_odds: dict,
) -> str:
    """Generate a final moderator summary after all rounds."""
    moderator = next((a for a in agents if a.is_moderator), None)
    if not moderator:
        return ""

    messages = [{"role": "system", "content": moderator.system_prompt}]
    for msg in history:
        messages.append({
            "role": "user",
            "content": f"[{msg.expert}]: {msg.content}",
        })

    odds_str = ", ".join(f"{k}: {v:.0%}" for k, v in polymarket_odds.items() if v)
    messages.append({
        "role": "user",
        "content": (
            "The debate has concluded. Provide the FINAL structured summary:\n\n"
            f"Current Polymarket odds: {odds_str}\n\n"
            "## Consensus\nWhat do all experts agree on?\n\n"
            "## Key Disagreements\nWhat points remain contested?\n\n"
            "## Final Probability Assessment\n"
            "Consensus probability for each outcome (must sum to 1.0).\n\n"
            "## Key Variables\n"
            "What 2-3 factors could most change the outcome?\n\n"
            "## Market Assessment\n"
            "Where does the Polymarket price deviate most from expert consensus?"
        ),
    })

    return await chat_completion(messages, temperature=0.4, max_tokens=1500)
