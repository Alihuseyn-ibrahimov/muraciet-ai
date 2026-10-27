"""vLLM ilə serve olunan modelin sürətini ölçür: TTFT, ümumi gecikmə, tokens/s, paralel yük.

İstifadə:  python -m finetune.benchmark --url http://gpu-server:8001/v1 --model muraciet-az --concurrency 8
"""
import argparse
import asyncio
import statistics
import time

from openai import AsyncOpenAI

PROMPTS = [
    "Küçəmizdə işıqlar yanmır, nə etməliyəm?",
    "Arayış almaq üçün hansı sənədlər lazımdır?",
    "Müraciətimə nə vaxt cavab veriləcək?",
    "Zibil bir həftədir daşınmır, şikayət etmək istəyirəm.",
]


async def one(client: AsyncOpenAI, model: str, prompt: str) -> dict:
    start = time.perf_counter()
    ttft, tokens = None, 0
    stream = await client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}], max_tokens=200, stream=True
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            ttft = ttft or time.perf_counter() - start
            tokens += 1  # təxmini: hər stream parçası ≈ 1 token
    total = time.perf_counter() - start
    return {"ttft": ttft or total, "total": total, "tokens": tokens}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8001/v1")
    ap.add_argument("--model", default="muraciet-az")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--requests", type=int, default=32)
    args = ap.parse_args()

    client = AsyncOpenAI(base_url=args.url, api_key="EMPTY")
    sem = asyncio.Semaphore(args.concurrency)

    async def guarded(i: int):
        async with sem:
            return await one(client, args.model, PROMPTS[i % len(PROMPTS)])

    wall = time.perf_counter()
    results = await asyncio.gather(*(guarded(i) for i in range(args.requests)))
    wall = time.perf_counter() - wall

    ttfts = sorted(r["ttft"] for r in results)
    totals = sorted(r["total"] for r in results)
    print(f"Sorğular: {len(results)}, paralellik: {args.concurrency}")
    print(f"TTFT   p50={statistics.median(ttfts)*1000:.0f} ms  p95={ttfts[int(len(ttfts)*0.95)-1]*1000:.0f} ms")
    print(f"Gecikmə p50={statistics.median(totals):.2f} s  p95={totals[int(len(totals)*0.95)-1]:.2f} s")
    print(f"Throughput: {sum(r['tokens'] for r in results)/wall:.1f} token/s, {len(results)/wall:.2f} sorğu/s")


if __name__ == "__main__":
    asyncio.run(main())
