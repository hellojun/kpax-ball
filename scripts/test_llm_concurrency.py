"""LLM 并发性能测试脚本 — GLM-4.7-FlashX

测试不同并发数下的：
- 首 token 延迟（TTFT）
- 总响应时间
- 吞吐量（tokens/sec）
- 成功率

用法：
  python scripts/test_llm_concurrency.py
  python scripts/test_llm_concurrency.py --concurrency 1,3,5,10 --rounds 3
"""

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field

import httpx

# ── 配置 ──────────────────────────────────────────────────────────────────

API_BASE = "https://open.bigmodel.cn/api/paas/v4"
MODEL = "glm-5-turbo"
API_KEY = os.environ.get("ZHIPUAI_API_KEY", "")

# 测试用的 prompt（模拟足球分析场景）
TEST_PROMPTS = [
    "分析曼城 vs 阿森纳的英超比赛，给出主胜、平局、客胜的概率预测，50字以内。",
    "利物浦本赛季的表现如何？用3个要点总结。",
    "Analyze the tactical matchup between Barcelona and Real Madrid. Keep it under 50 words.",
    "What are the key factors in a World Cup knockout stage match? 3 bullet points.",
    "评估拜仁慕尼黑在欧冠的夺冠概率，考虑阵容深度和伤病情况，50字以内。",
    "Compare Haaland and Mbappe's current season stats in 3 sentences.",
    "中超联赛的竞争力如何？和五大联赛相比有什么差距？30字以内。",
    "Predict the outcome of Juventus vs AC Milan, considering recent form. Under 50 words.",
]


@dataclass
class RequestResult:
    success: bool
    ttft_ms: float = 0          # Time To First Token
    total_ms: float = 0         # 总响应时间
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str = ""


@dataclass
class ConcurrencyResult:
    concurrency: int
    results: list[RequestResult] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def success_rate(self) -> float:
        return self.success_count / len(self.results) * 100 if self.results else 0

    @property
    def avg_ttft(self) -> float:
        vals = [r.ttft_ms for r in self.results if r.success and r.ttft_ms > 0]
        return sum(vals) / len(vals) if vals else 0

    @property
    def avg_total(self) -> float:
        vals = [r.total_ms for r in self.results if r.success]
        return sum(vals) / len(vals) if vals else 0

    @property
    def p95_total(self) -> float:
        vals = sorted([r.total_ms for r in self.results if r.success])
        if not vals:
            return 0
        idx = int(len(vals) * 0.95)
        return vals[min(idx, len(vals) - 1)]

    @property
    def avg_tokens_per_sec(self) -> float:
        vals = [r.completion_tokens / (r.total_ms / 1000) for r in self.results if r.success and r.total_ms > 0]
        return sum(vals) / len(vals) if vals else 0

    @property
    def total_tokens(self) -> int:
        return sum(r.completion_tokens for r in self.results if r.success)


# ── 非流式请求 ─────────────────────────────────────────────────────────────

async def call_llm_non_stream(client: httpx.AsyncClient, prompt: str) -> RequestResult:
    start = time.perf_counter()
    try:
        resp = await client.post(
            f"{API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1000,
                "temperature": 0.7,
            },
            timeout=120,
        )
        total_ms = (time.perf_counter() - start) * 1000

        if resp.status_code != 200:
            return RequestResult(success=False, total_ms=total_ms, error=f"HTTP {resp.status_code}: {resp.text[:100]}")

        data = resp.json()
        usage = data.get("usage", {})
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        return RequestResult(
            success=bool(content),
            total_ms=total_ms,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            error="" if content else "empty content",
        )
    except Exception as e:
        total_ms = (time.perf_counter() - start) * 1000
        return RequestResult(success=False, total_ms=total_ms, error=str(e)[:100])


# ── 流式请求（测 TTFT）────────────────────────────────────────────────────

async def call_llm_stream(client: httpx.AsyncClient, prompt: str) -> RequestResult:
    start = time.perf_counter()
    ttft = 0
    completion_tokens = 0
    content_parts = []

    try:
        async with client.stream(
            "POST",
            f"{API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1000,
                "temperature": 0.7,
                "stream": True,
            },
            timeout=120,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                total_ms = (time.perf_counter() - start) * 1000
                return RequestResult(success=False, total_ms=total_ms, error=f"HTTP {resp.status_code}: {body[:100]}")

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if delta:
                        if not ttft:
                            ttft = (time.perf_counter() - start) * 1000
                        content_parts.append(delta)
                        completion_tokens += 1  # 近似
                except json.JSONDecodeError:
                    pass

        total_ms = (time.perf_counter() - start) * 1000
        content = "".join(content_parts)
        return RequestResult(
            success=bool(content),
            ttft_ms=ttft,
            total_ms=total_ms,
            completion_tokens=completion_tokens,
            error="" if content else "empty content",
        )
    except Exception as e:
        total_ms = (time.perf_counter() - start) * 1000
        return RequestResult(success=False, total_ms=total_ms, error=str(e)[:100])


# ── 并发测试 ──────────────────────────────────────────────────────────────

async def run_concurrency_test(
    concurrency: int,
    num_requests: int,
    use_stream: bool,
) -> ConcurrencyResult:
    result = ConcurrencyResult(concurrency=concurrency)

    async with httpx.AsyncClient() as client:
        call_fn = call_llm_stream if use_stream else call_llm_non_stream
        semaphore = asyncio.Semaphore(concurrency)

        async def limited_call(prompt: str) -> RequestResult:
            async with semaphore:
                return await call_fn(client, prompt)

        # 生成足够多的 prompt
        prompts = [TEST_PROMPTS[i % len(TEST_PROMPTS)] for i in range(num_requests)]

        # 并发执行
        tasks = [limited_call(p) for p in prompts]
        results = await asyncio.gather(*tasks)
        result.results = list(results)

    return result


# ── 打印结果 ──────────────────────────────────────────────────────────────

def print_result(r: ConcurrencyResult, use_stream: bool):
    print(f"\n{'='*60}")
    print(f"  并发数: {r.concurrency}  |  请求数: {len(r.results)}  |  模式: {'流式' if use_stream else '非流式'}")
    print(f"{'='*60}")
    print(f"  成功率:      {r.success_rate:.1f}% ({r.success_count}/{len(r.results)})")
    print(f"  平均耗时:    {r.avg_total:.0f}ms")
    print(f"  P95 耗时:    {r.p95_total:.0f}ms")
    if use_stream:
        print(f"  平均 TTFT:   {r.avg_ttft:.0f}ms")
    print(f"  平均速度:    {r.avg_tokens_per_sec:.1f} tokens/sec")
    print(f"  总 tokens:   {r.total_tokens}")

    # 显示失败详情
    failures = [r for r in r.results if not r.success]
    if failures:
        print(f"\n  失败详情:")
        error_counts: dict[str, int] = {}
        for f in failures:
            error_counts[f.error] = error_counts.get(f.error, 0) + 1
        for err, count in error_counts.items():
            print(f"    [{count}x] {err}")


def print_summary(all_results: list[ConcurrencyResult], use_stream: bool):
    print(f"\n\n{'#'*60}")
    print(f"  汇总对比 — {MODEL} ({'流式' if use_stream else '非流式'})")
    print(f"{'#'*60}")
    print(f"  {'并发':>4}  {'成功率':>6}  {'平均耗时':>8}  {'P95':>8}  {'TTFT':>8}  {'tok/s':>6}")
    print(f"  {'-'*50}")
    for r in all_results:
        ttft_str = f"{r.avg_ttft:.0f}ms" if use_stream else "N/A"
        print(f"  {r.concurrency:>4}  {r.success_rate:>5.1f}%  {r.avg_total:>7.0f}ms  {r.p95_total:>7.0f}ms  {ttft_str:>8}  {r.avg_tokens_per_sec:>5.1f}")


# ── 主入口 ─────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="LLM 并发性能测试")
    parser.add_argument("--concurrency", default="1,3,5,10", help="并发数列表，逗号分隔")
    parser.add_argument("--requests", type=int, default=10, help="每轮请求总数")
    parser.add_argument("--rounds", type=int, default=1, help="重复轮数（取平均）")
    parser.add_argument("--stream", action="store_true", help="使用流式请求（测 TTFT）")
    parser.add_argument("--key", default="", help="ZhipuAI API Key（也可用环境变量 ZHIPUAI_API_KEY）")
    args = parser.parse_args()

    global API_KEY
    if args.key:
        API_KEY = args.key
    if not API_KEY:
        # 尝试从 .env 读取
        env_path = os.path.join(os.path.dirname(__file__), "..", "backend", ".env")
        if os.path.exists(env_path):
            for line in open(env_path):
                if line.startswith("ZHIPUAI_API_KEY="):
                    API_KEY = line.strip().split("=", 1)[1]
                    break
    if not API_KEY:
        print("错误：需要 ZHIPUAI_API_KEY。使用 --key 参数或设置环境变量。")
        sys.exit(1)

    concurrency_levels = [int(x.strip()) for x in args.concurrency.split(",")]

    print(f"LLM 并发测试")
    print(f"模型: {MODEL}")
    print(f"并发级别: {concurrency_levels}")
    print(f"每轮请求数: {args.requests}")
    print(f"轮数: {args.rounds}")
    print(f"模式: {'流式' if args.stream else '非流式'}")
    print(f"API: {API_BASE}")

    # 预热（1 次请求确保连接正常）
    print("\n预热中...")
    async with httpx.AsyncClient() as client:
        warmup = await call_llm_non_stream(client, "Hi")
        if warmup.success:
            print(f"预热成功: {warmup.total_ms:.0f}ms")
        else:
            print(f"预热失败: {warmup.error}")
            print("检查 API Key 和网络连接")
            sys.exit(1)

    all_results: list[ConcurrencyResult] = []

    for level in concurrency_levels:
        round_results: list[ConcurrencyResult] = []

        for rd in range(args.rounds):
            if args.rounds > 1:
                print(f"\n--- 并发 {level}, 第 {rd+1}/{args.rounds} 轮 ---")

            result = await run_concurrency_test(level, args.requests, args.stream)
            round_results.append(result)
            print_result(result, args.stream)

        # 多轮取平均
        if args.rounds > 1:
            merged = ConcurrencyResult(concurrency=level)
            for rr in round_results:
                merged.results.extend(rr.results)
            all_results.append(merged)
        else:
            all_results.append(round_results[0])

    print_summary(all_results, args.stream)


if __name__ == "__main__":
    asyncio.run(main())
