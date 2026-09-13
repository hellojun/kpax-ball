"""Multi-model AI abstraction layer using LiteLLM.

Adapted from Agentxlab for KPAX Ball football analysis.
Provides generic chat_completion + football-specific prompt builders.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import litellm

from app.config import settings

logger = logging.getLogger(__name__)

if settings.zhipuai_api_key and "ZHIPUAI_API_KEY" not in os.environ:
    os.environ["ZHIPUAI_API_KEY"] = settings.zhipuai_api_key


async def chat_completion(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    retries: int = 2,
) -> str:
    """Generic LLM call with retry."""
    model = model or settings.default_ai_model

    for attempt in range(1, retries + 2):
        try:
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=settings.zhipuai_api_key,
                api_base=settings.zhipuai_api_base,
            )
            content = response.choices[0].message.content

            usage = getattr(response, "usage", None)
            if usage:
                logger.info(
                    "LLM call: model=%s tokens=%d (prompt=%d, completion=%d)",
                    model,
                    usage.total_tokens,
                    usage.prompt_tokens,
                    usage.completion_tokens,
                )

            return content
        except Exception as exc:
            if attempt > retries:
                raise
            logger.warning("LLM call attempt %d failed (%s), retrying...", attempt, exc)
            await asyncio.sleep(2 * attempt)


async def chat_completion_json(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2000,
) -> dict:
    """LLM call that returns parsed JSON. Uses non-streaming for reliability."""
    model = model or settings.default_ai_model

    for attempt in range(1, 4):
        try:
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=settings.zhipuai_api_key,
                api_base=settings.zhipuai_api_base,
            )
            raw = response.choices[0].message.content or ""

            usage = getattr(response, "usage", None)
            if usage:
                logger.info(
                    "LLM JSON call: model=%s tokens=%d (prompt=%d, completion=%d)",
                    model, usage.total_tokens, usage.prompt_tokens, usage.completion_tokens,
                )

            # 尝试直接解析
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass

            # fallback：从 markdown 包裹或前后文本中提取 JSON
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(raw[start:end])
                except json.JSONDecodeError:
                    pass

            # fallback 2：截断的 JSON 修复（补全缺失的括号）
            if start >= 0:
                truncated = raw[start:]
                repaired = _repair_truncated_json(truncated)
                if repaired:
                    logger.info("Repaired truncated JSON (attempt %d)", attempt)
                    return repaired

            logger.warning("JSON parse failed (attempt %d), raw: %s", attempt, raw[:300])
            if attempt >= 3:
                raise ValueError(f"Failed to parse JSON from LLM response: {raw[:300]}")
        except ValueError:
            raise
        except Exception as exc:
            if attempt >= 3:
                raise
            logger.warning("LLM JSON call attempt %d failed (%s), retrying...", attempt, exc)
            await asyncio.sleep(1)


async def _stream_until_json(
    model: str, messages: list[dict], temperature: float, max_tokens: int
) -> dict | None:
    """Stream LLM response, parse JSON on-the-fly, abort as soon as JSON is complete.

    大幅减少等待时间：模型输出 ~70 token 完成 JSON 后立刻返回，
    不再等剩余 ~500 token 的废话生成完。
    """
    buffer = ""
    brace_depth = 0
    json_start = -1

    response = await litellm.acompletion(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=settings.zhipuai_api_key,
        api_base=settings.zhipuai_api_base,
        stream=True,
    )

    async for chunk in response:
        delta = chunk.choices[0].delta.content or ""
        buffer += delta

        for ch in delta:
            if ch == "{":
                if json_start < 0:
                    json_start = buffer.index("{")
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth == 0 and json_start >= 0:
                    # JSON 对象闭合，立刻解析并返回
                    candidate = buffer[json_start : buffer.index("}",
                        json_start + 1 if len(buffer) > json_start + 1 else json_start) + 1]
                    # 用 rfind 取最后一个 } 对应的完整对象
                    end = buffer.rfind("}") + 1
                    candidate = buffer[json_start:end]
                    try:
                        result = json.loads(candidate)
                        logger.info("JSON extracted via streaming at %d chars (buffer %d)", len(candidate), len(buffer))
                        return result
                    except json.JSONDecodeError:
                        pass  # 继续接收

    # stream 结束，最后尝试从完整 buffer 提取
    if json_start >= 0:
        end = buffer.rfind("}") + 1
        if end > json_start:
            try:
                return json.loads(buffer[json_start:end])
            except json.JSONDecodeError:
                pass

    # 流式返回空内容（reasoning 模型常见），回退到非流式调用
    if not buffer.strip():
        logger.info("Stream returned empty buffer, falling back to non-streaming call")
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=settings.zhipuai_api_key,
            api_base=settings.zhipuai_api_base,
        )
        raw = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        if usage:
            logger.info(
                "LLM JSON fallback: model=%s tokens=%d (prompt=%d, completion=%d)",
                model, usage.total_tokens, usage.prompt_tokens, usage.completion_tokens,
            )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(raw[start:end])
                except json.JSONDecodeError:
                    pass
        logger.warning("Non-streaming fallback also failed, raw: %s", raw[:300])
        return None

    logger.warning("Stream completed without valid JSON, buffer: %s", buffer[:300])
    return None


def _repair_truncated_json(text: str) -> dict | None:
    """尝试修复被截断的 JSON：逐步去掉尾部字符直到能解析。"""
    # 先补全可能缺失的括号
    for suffix in [']}', '"}]}', '"}}]}', '"]}}]}', '"}],"key_variables":[],"disagreements":[]}']:
        try:
            return json.loads(text + suffix)
        except json.JSONDecodeError:
            continue

    # 从末尾逐字符回退，找到最后一个可解析的位置
    for i in range(len(text) - 1, max(0, len(text) - 200), -1):
        ch = text[i]
        if ch in ('}', ']'):
            try:
                return json.loads(text[: i + 1])
            except json.JSONDecodeError:
                continue

    return None
