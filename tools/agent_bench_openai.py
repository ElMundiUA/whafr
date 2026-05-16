"""OpenAI-flavoured agent bench (GPT-5 / GPT-5-mini / GPT-5.2).

Same 35-task catalogue, same 4-stage agentic loop, same Lighthouse
tool. Differences vs ``agent_bench_v2``:

- Uses OpenAI ``chat.completions`` with function-calling.
- Pricing math swapped to OpenAI table.
- Judge can be either provider — we default to Claude Sonnet for
  judge so verdicts stay comparable across runs (Sonnet was the judge
  for the v2 Anthropic runs too; using the same judge controls for
  rubric drift between model families).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Reuse task catalogue + helpers from the Anthropic bench so the test
# matrix is identical across model families. When run as a script the
# ``tools`` package isn't on sys.path; add it explicitly so the import
# works whether invoked as ``python tools/agent_bench_openai.py`` or
# ``python -m tools.agent_bench_openai``.
sys_path_added = False
import os
import sys as _sys

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in _sys.path:
    _sys.path.insert(0, _here)
    sys_path_added = True

from agent_bench_v2 import (  # type: ignore
    LIGHTHOUSE_INSTRUCTIONS,
    LIBRARY_SEARCH_TOOL,
    FETCH_SOURCE_TOOL,
    TASKS,
    CallTelemetry,
    RunTelemetry,
    load_role_prompt,
    render_report,
    _dispatch_tool,
)

logger = logging.getLogger(__name__)


CONCURRENCY = 5
MAX_TOOL_LOOP = 10


# OpenAI pricing (gpt-5 family, May 2026). Tweak if pricing changes.
# https://openai.com/pricing
PRICING_OPENAI = {
    "gpt-5": {"input": 5.0 / 1e6, "output": 20.0 / 1e6, "cached_input": 0.50 / 1e6},
    "gpt-5-mini": {"input": 0.50 / 1e6, "output": 2.0 / 1e6, "cached_input": 0.05 / 1e6},
    "gpt-5.2": {"input": 5.0 / 1e6, "output": 20.0 / 1e6, "cached_input": 0.50 / 1e6},
}


def _pricing_for(model: str) -> dict[str, float]:
    if model in PRICING_OPENAI:
        return PRICING_OPENAI[model]
    # Fallback: assume gpt-5 tier
    return PRICING_OPENAI["gpt-5"]


def _openai_cost(call_data: dict, model: str) -> float:
    p = _pricing_for(model)
    cached = call_data.get("cache_read", 0)
    uncached_in = call_data.get("input_tokens", 0)
    out = call_data.get("output_tokens", 0)
    return uncached_in * p["input"] + cached * p["cached_input"] + out * p["output"]


# OpenAI tool spec (function-calling shape).
def _openai_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": LIBRARY_SEARCH_TOOL["name"],
                "description": LIBRARY_SEARCH_TOOL["description"],
                "parameters": LIBRARY_SEARCH_TOOL["input_schema"],
            },
        },
        {
            "type": "function",
            "function": {
                "name": FETCH_SOURCE_TOOL["name"],
                "description": FETCH_SOURCE_TOOL["description"],
                "parameters": FETCH_SOURCE_TOOL["input_schema"],
            },
        },
    ]


def _is_reasoning_model(model: str) -> bool:
    """Reasoning models eat the output budget on chain-of-thought before
    emitting visible content. They need 3-4× the budget plus an explicit
    reasoning-effort hint, or the final ``message.content`` comes back empty.
    """
    m = model.lower()
    return any(p in m for p in ("gpt-5", "kimi", "deepseek-r", "o1", "o3", "gpt-oss", "thinking"))


async def _call_openai(
    client,
    *,
    model: str,
    system_text: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.0,
    stage: str = "?",
) -> tuple[Any, CallTelemetry]:
    """One OpenAI chat-completions call with usage capture.

    Includes a small retry loop on 429 (rate limit) — necessary on
    OpenRouter free tier where per-minute caps are aggressive (8-20
    rpm). Without this every free-model bench dies in the first minute.
    """
    t0 = time.monotonic()
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system_text}, *messages],
    }
    # Reasoning models need much more budget — reasoning tokens count against
    # max_completion_tokens, and the visible content gets truncated to empty
    # if reasoning eats the whole budget first.
    is_reasoning = _is_reasoning_model(model)
    effective_max = max_tokens * 4 if is_reasoning else max_tokens
    # GPT-5 family rejects ``max_tokens`` and uses ``max_completion_tokens``.
    # OpenRouter / Gemini / DeepSeek / etc still accept ``max_tokens``.
    if model.startswith("gpt-5"):
        kwargs["max_completion_tokens"] = effective_max
        kwargs["reasoning_effort"] = "low"
    else:
        kwargs["max_tokens"] = effective_max
        # Non-GPT-5 models accept temperature freely.
        if temperature != 1.0:
            kwargs["temperature"] = temperature
        # OpenRouter exposes a unified reasoning param across providers.
        # Use ``effort:low`` to leave room for visible content.
        if is_reasoning:
            kwargs["extra_body"] = {"reasoning": {"effort": "low"}}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    # Retry on 429 (rate-limit). OpenRouter free tier is 8-20 rpm, so
    # bursts of ~5 concurrent tasks × 4 stages will hit the cap. Back
    # off with jitter so we don't all retry on the exact same second.
    import random as _rnd

    resp = None
    last_exc: Exception | None = None
    for attempt in range(6):
        try:
            resp = await client.chat.completions.create(**kwargs)
            break
        except Exception as exc:  # noqa: BLE001
            err_str = repr(exc)
            if "429" not in err_str and "rate" not in err_str.lower():
                raise
            last_exc = exc
            wait = (2 ** attempt) + _rnd.uniform(0.5, 2.5)
            await asyncio.sleep(min(wait, 65.0))
    if resp is None:
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("OpenAI call failed after retries")
    elapsed = (time.monotonic() - t0) * 1000

    usage = resp.usage
    cached = 0
    # OpenRouter occasionally returns usage=None on tool-use rounds; treat
    # it as zero rather than crashing the whole bench. The same fallback
    # covers OpenAI's empty-usage edge case during streamed completions.
    if usage is None:
        class _Empty:
            prompt_tokens = 0
            completion_tokens = 0
            prompt_tokens_details = None
        usage = _Empty()
    # OpenAI usage shape: usage.prompt_tokens_details may carry cached_tokens
    if hasattr(usage, "prompt_tokens_details"):
        cached = (
            getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0
        )
    tool_calls = 0
    if resp.choices and resp.choices[0].message and resp.choices[0].message.tool_calls:
        tool_calls = len(resp.choices[0].message.tool_calls)
    tel = CallTelemetry(
        stage=stage,
        duration_ms=elapsed,
        input_tokens=max(0, (usage.prompt_tokens or 0) - cached),
        output_tokens=usage.completion_tokens or 0,
        cache_read=cached,
        cache_write=0,  # OpenAI caching is implicit; no separate write counter
        tool_calls=tool_calls,
    )
    return resp, tel


def _openai_text(resp) -> str:
    if not resp.choices:
        return ""
    msg = resp.choices[0].message
    return (msg.content or "").strip()


def _openai_tool_uses(resp) -> list:
    if not resp.choices:
        return []
    return resp.choices[0].message.tool_calls or []


async def openai_agentic_run(
    client,
    *,
    task: dict[str, Any],
    role_prompt: str,
    mode: str,
    graph,
    model: str,
) -> RunTelemetry:
    tel = RunTelemetry(mode=mode)
    if mode == "B":
        system_text = role_prompt + LIGHTHOUSE_INSTRUCTIONS
        tools = _openai_tools()
    else:
        system_text = role_prompt
        tools = None

    task_user = f"# {task['title']}\n\n{task['description']}"

    # --------- Stage 1: PLAN ---------
    plan_user = (
        task_user
        + "\n\n---\n\nFirst, draft a 3-5 step PLAN. Numbered list, one line per step. "
        "Do NOT answer the task yet."
    )
    plan_resp, plan_tel = await _call_openai(
        client,
        model=model,
        system_text=system_text,
        messages=[{"role": "user", "content": plan_user}],
        tools=tools,
        max_tokens=600,
        stage="plan",
    )
    tel.calls.append(plan_tel)
    tel.plan = _openai_text(plan_resp)

    # --------- Stage 2: EXECUTE w/ tool-use loop ---------
    execute_user = (
        task_user
        + "\n\n## Your plan\n"
        + tel.plan
        + "\n\n---\n\nNow execute the plan. Produce your full draft answer."
        + (
            " Use library_search to find facts; when a hit looks "
            "relevant but its one-line summary is too thin, fetch_source "
            "on its ep id to read the full article."
            if mode == "B"
            else ""
        )
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": execute_user}]
    accumulated_text: list[str] = []
    tool_uses_pending: list = []
    resp = None
    for _ in range(MAX_TOOL_LOOP):
        resp, et = await _call_openai(
            client,
            model=model,
            system_text=system_text,
            messages=messages,
            tools=tools,
            max_tokens=2000,
            stage="execute",
        )
        tel.calls.append(et)
        round_text = _openai_text(resp)
        if round_text:
            accumulated_text.append(round_text)
        tool_uses_pending = _openai_tool_uses(resp)
        if not tool_uses_pending:
            break

        # Append assistant turn with tool_calls
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": round_text or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_uses_pending
            ],
        }
        messages.append(assistant_msg)

        # Run each tool, append tool results
        for tc in tool_uses_pending:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            payload = await _dispatch_tool(
                graph=graph, name=tc.function.name, input_=args, tel=tel
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": payload,
                }
            )

    # Force-final pass if loop exhausted with tools still pending or empty draft
    draft_text = "\n\n".join(accumulated_text).strip()
    if tool_uses_pending or len(draft_text) < 200:
        # If tool_uses still pending, we already added the assistant turn
        # above; satisfying the call requires tool results. If they're
        # not satisfied yet, skip and ask without tools.
        messages.append(
            {
                "role": "user",
                "content": (
                    "Stop calling tools. Produce your full final draft answer "
                    "to the original task NOW."
                ),
            }
        )
        resp, et = await _call_openai(
            client,
            model=model,
            system_text=system_text,
            messages=messages,
            tools=None,
            max_tokens=3000,
            stage="execute_force",
        )
        tel.calls.append(et)
        force_text = _openai_text(resp)
        if force_text:
            accumulated_text.append(force_text)
        draft_text = "\n\n".join(accumulated_text).strip()

    # --------- Stage 3: SELF-REVIEW ---------
    review_messages = list(messages)
    review_messages.append({"role": "assistant", "content": draft_text})
    review_messages.append(
        {
            "role": "user",
            "content": (
                "Critique your own draft above. Identify 1-3 specific weaknesses. "
                "Output just the critique, not a rewrite."
            ),
        }
    )
    review_resp, rev_tel = await _call_openai(
        client,
        model=model,
        system_text=system_text,
        messages=review_messages,
        tools=None,
        max_tokens=400,
        stage="review",
    )
    tel.calls.append(rev_tel)
    tel.review_notes = _openai_text(review_resp)

    # --------- Stage 4: FINALIZE ---------
    final_messages = list(review_messages)
    final_messages.append({"role": "assistant", "content": tel.review_notes})
    final_messages.append(
        {
            "role": "user",
            "content": "Produce the final answer addressing the critique. Concise, concrete.",
        }
    )
    final_resp, fin_tel = await _call_openai(
        client,
        model=model,
        system_text=system_text,
        messages=final_messages,
        tools=None,
        max_tokens=2000,
        stage="finalize",
    )
    tel.calls.append(fin_tel)
    tel.final = _openai_text(final_resp)

    return tel


# Use Claude Sonnet for judging across all model families so verdicts
# stay comparable.
JUDGE_RUBRIC = """\
You compare two answers to the same task. Score each on four axes
0-10 (total 0-40):

1. specificity
2. citation
3. actionability
4. accuracy

Reply strict JSON only:

{"a":{"specificity":0-10,"citation":0-10,"actionability":0-10,"accuracy":0-10,"total":0-40},
 "b":{"specificity":0-10,"citation":0-10,"actionability":0-10,"accuracy":0-10,"total":0-40},
 "winner":"a"|"b"|"tie","rationale":"<= 40 words"}
"""


async def judge_pair_with_anthropic(
    anth_client,
    *,
    task: dict[str, Any],
    answer_a: str,
    answer_b: str,
    judge_model: str = "claude-sonnet-4-6",
) -> tuple[dict[str, Any], CallTelemetry]:
    t0 = time.monotonic()
    resp = await anth_client.messages.create(
        model=judge_model,
        max_tokens=500,
        temperature=0.0,
        system=[{"type": "text", "text": JUDGE_RUBRIC, "cache_control": {"type": "ephemeral"}}],
        messages=[
            {
                "role": "user",
                "content": (
                    f"## Task ({task['role']}, {task['type']}, {task['complexity']})\n"
                    f"{task['title']}\n\n{task['description']}\n\n"
                    f"## Answer A (no-lighthouse)\n{answer_a[:6000]}\n\n"
                    f"## Answer B (with-lighthouse)\n{answer_b[:6000]}"
                ),
            }
        ],
    )
    elapsed = (time.monotonic() - t0) * 1000
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n|\n```$", "", text)
    try:
        verdict = json.loads(text)
    except json.JSONDecodeError:
        # Judge sometimes emits preamble + JSON, or two JSON blobs back-to-back.
        # Walk every "{...}" candidate (innermost first), try to parse each, take
        # the first one that has the keys we need.
        verdict = None
        for match in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL):
            try:
                cand = json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
            if isinstance(cand, dict) and "winner" in cand:
                verdict = cand
                break
        if verdict is None:
            verdict = {"winner": "tie", "rationale": "unparseable", "_raw": text}
    tel = CallTelemetry(
        stage="judge",
        duration_ms=elapsed,
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        cache_read=getattr(resp.usage, "cache_read_input_tokens", 0),
        cache_write=getattr(resp.usage, "cache_creation_input_tokens", 0),
    )
    return verdict, tel


async def run_task_pair(
    oai_client,
    anth_client,
    task_idx: int,
    task: dict[str, Any],
    role_prompt: str,
    graph,
    model: str,
    out_dir: Path,
    progress_lock: asyncio.Lock,
    progress_state: dict,
) -> dict[str, Any]:
    a = await openai_agentic_run(
        oai_client, task=task, role_prompt=role_prompt, mode="A", graph=graph, model=model
    )
    b = await openai_agentic_run(
        oai_client, task=task, role_prompt=role_prompt, mode="B", graph=graph, model=model
    )
    verdict, judge_tel = await judge_pair_with_anthropic(
        anth_client, task=task, answer_a=a.final, answer_b=b.final
    )
    rec = {
        "task_idx": task_idx,
        "task": task,
        "model": model,
        "a": asdict(a),
        "b": asdict(b),
        "verdict": verdict,
        "judge_telemetry": asdict(judge_tel),
    }
    (out_dir / f"task-{task_idx:02d}.json").write_text(json.dumps(rec, indent=2))
    async with progress_lock:
        progress_state["done"] += 1
        ar = verdict.get("a", {}).get("total", 0)
        br = verdict.get("b", {}).get("total", 0)
        win = verdict.get("winner", "?")
        print(
            f"  [{progress_state['done']:>2}/{progress_state['total']}] "
            f"{task['role']:<16s} {task['type']:<12s} "
            f"{task['title'][:32]:<32s} "
            f"A={ar:>2} B={br:>2} → {win:<3s} "
            f"calls={a.call_count}/{b.call_count}",
            flush=True,
        )
    return rec


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="OpenAI/OpenRouter model id")
    parser.add_argument("--task-type", action="append", default=None)
    parser.add_argument("--role", action="append", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--provider",
        choices=["openai", "openrouter"],
        default="openai",
        help="Backend: openai (default) or openrouter (uses "
        "OPENROUTER_API_KEY + openrouter.ai/api/v1).",
    )
    parser.add_argument(
        "--rerun-tasks",
        default=None,
        help="Comma-separated 1-based task indices to rerun (e.g. '21,22,25'). "
             "Requires --out-dir; writes results into that dir and re-renders report.md.",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Existing run dir to write into (used with --rerun-tasks). "
             "If omitted a fresh dir is created.",
    )
    args = parser.parse_args()

    selected = list(TASKS)
    rerun_indices: set[int] | None = None
    if args.rerun_tasks:
        rerun_indices = {int(s) for s in args.rerun_tasks.split(",") if s.strip()}
        # Reruns are tasks by 1-based idx; replace selected with that subset
        selected = [t for i, t in enumerate(TASKS, 1) if i in rerun_indices]
        if not selected:
            print(f"--rerun-tasks {sorted(rerun_indices)} matched no tasks", file=sys.stderr)
            return 1
    if args.task_type:
        wanted = set(args.task_type)
        selected = [t for t in selected if t["type"] in wanted]
    if args.role:
        wanted = set(args.role)
        selected = [t for t in selected if t["role"] in wanted]
    if args.limit:
        selected = selected[: args.limit]

    logging.basicConfig(level=logging.WARNING)
    from anthropic import AsyncAnthropic
    from openai import AsyncOpenAI

    from lighthouse.core.config import get_settings
    from lighthouse.core.graph import KnowledgeGraph

    settings = get_settings()
    if args.provider == "openrouter":
        if not settings.openrouter_api_key:
            print("OPENROUTER_API_KEY missing — abort", file=sys.stderr)
            return 1
        api_key = settings.openrouter_api_key
        base_url = settings.openrouter_base_url
        # OpenRouter expects HTTP-Referer and X-Title for attribution.
        # The OpenAI SDK accepts extra default_headers.
        default_headers = {
            "HTTP-Referer": "https://github.com/ElMundiUA/lighthouse",
            "X-Title": "lighthouse-agent-bench",
        }
    else:
        if not settings.openai_api_key:
            print("OPENAI_API_KEY missing — abort", file=sys.stderr)
            return 1
        api_key = settings.openai_api_key
        base_url = None
        default_headers = None
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY missing (judge needs it) — abort", file=sys.stderr)
        return 1

    oai_kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        oai_kwargs["base_url"] = base_url
    if default_headers:
        oai_kwargs["default_headers"] = default_headers
    oai_client = AsyncOpenAI(**oai_kwargs)
    anth_client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    graph = KnowledgeGraph(settings)
    await graph.initialize()

    if args.out_dir:
        out_dir = Path(args.out_dir)
        if not out_dir.exists():
            print(f"--out-dir {out_dir} does not exist", file=sys.stderr)
            return 1
    else:
        run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + f"-{args.model}"
        out_dir = Path("tools/eval/agent_bench") / run_id
        out_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"OpenAI bench — {len(selected)} tasks (of {len(TASKS)}), "
        f"agent={args.model}, judge=claude-sonnet-4-6, parallel={CONCURRENCY}"
    )
    print(f"Output: {out_dir}\n")

    role_prompts: dict[str, str] = {}
    for t in selected:
        if t["role"] not in role_prompts:
            role_prompts[t["role"]] = load_role_prompt(t["role"])

    sem = asyncio.Semaphore(CONCURRENCY)
    progress_lock = asyncio.Lock()
    progress_state = {"done": 0, "total": len(selected)}

    async def gated(idx, task):
        async with sem:
            return await run_task_pair(
                oai_client,
                anth_client,
                idx,
                task,
                role_prompts[task["role"]],
                graph,
                args.model,
                out_dir,
                progress_lock,
                progress_state,
            )

    # Use the ORIGINAL TASKS index (1-based) so rerun files overwrite
    # the right task-NN.json slots in --out-dir.
    task_to_idx = {id(t): i + 1 for i, t in enumerate(TASKS)}

    t0 = time.monotonic()
    results = await asyncio.gather(*(gated(task_to_idx[id(t)], t) for t in selected))
    elapsed = time.monotonic() - t0
    await graph.close()

    print(f"\nFinished {len(results)} tasks in {elapsed / 60:.1f} min")
    # When rerunning into an existing dir, the report must include the
    # untouched task files too — reload everything from disk.
    if args.rerun_tasks:
        all_records = []
        for tp in sorted(out_dir.glob("task-*.json")):
            try:
                all_records.append(json.loads(tp.read_text()))
            except Exception:
                pass
        report_md = render_report(all_records, out_dir)
    else:
        report_md = render_report(results, out_dir)
    (out_dir / "report.md").write_text(report_md)
    print(report_md)
    print(f"\nReport: {out_dir}/report.md")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
