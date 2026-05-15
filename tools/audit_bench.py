#!/usr/bin/env python3
"""Audit finished bench runs: classify each task as OK / DSQ-A / DSQ-B / DSQ-both,
re-aggregate headline numbers excluding DSQs.

Usage:
    python tools/audit_bench.py                       # audit all recent runs
    python tools/audit_bench.py path/to/run-dir       # one run
    python tools/audit_bench.py --rerun-list out.json # also dump (model, idx) pairs
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNS = ROOT / "tools" / "eval" / "agent_bench"


def is_degenerate(text: str | None) -> tuple[bool, str]:
    """Return (is_degenerate, reason)."""
    t = (text or "").strip()
    if not t:
        return True, "empty"
    if len(t) < 80:
        return True, f"too_short({len(t)}c)"
    # tool-use leak: first non-ws is "{" and text has tool-call signature
    head = t[:300]
    if head.lstrip().startswith("{") and (
        '"tool_use"' in head
        or '"name"' in head and '"input"' in head
        or '"type": "tool_use"' in head
    ):
        return True, "tool_use_leak"
    # plain refusal / non-answer
    refusals = (
        "i cannot",
        "i can't help",
        "i'm sorry, but i can't",
        "i am not able to",
    )
    # Only short blob-refusals are DSQ; long "I cannot proceed without X"
    # is a legitimate clarification request from e.g. the clarification role.
    if len(t) < 300 and t.lower().startswith(refusals):
        return True, "refusal"
    # all-numeric-zero score with content existing — judge problem, not answer problem
    return False, ""


def audit_run(run_dir: Path) -> dict:
    tasks = sorted(run_dir.glob("task-*.json"))
    if not tasks:
        return {"run": str(run_dir), "tasks": [], "skip": True}

    rows = []
    for tp in tasks:
        try:
            d = json.loads(tp.read_text())
        except Exception as e:
            rows.append({"idx": tp.stem, "error": f"parse: {e}"})
            continue
        a_text = (d.get("a") or {}).get("final", "")
        b_text = (d.get("b") or {}).get("final", "")
        a_bad, a_reason = is_degenerate(a_text)
        b_bad, b_reason = is_degenerate(b_text)
        verdict = d.get("verdict") or {}
        score_a = (verdict.get("a") or {}).get("total")
        score_b = (verdict.get("b") or {}).get("total")
        winner = verdict.get("winner")
        rows.append(
            {
                "idx": d.get("task_idx"),
                "role": (d.get("task") or {}).get("role"),
                "type": (d.get("task") or {}).get("type"),
                "score_a": score_a,
                "score_b": score_b,
                "winner": winner,
                "a_bad": a_bad,
                "b_bad": b_bad,
                "a_reason": a_reason,
                "b_reason": b_reason,
                "len_a": len(a_text or ""),
                "len_b": len(b_text or ""),
            }
        )

    ok = [r for r in rows if not r.get("error") and not r["a_bad"] and not r["b_bad"]]
    dsq_a = [r for r in rows if r.get("a_bad") and not r.get("b_bad")]
    dsq_b = [r for r in rows if r.get("b_bad") and not r.get("a_bad")]
    dsq_both = [r for r in rows if r.get("a_bad") and r.get("b_bad")]

    def headline(rows_):
        if not rows_:
            return None
        a_scores = [r["score_a"] for r in rows_ if r["score_a"] is not None]
        b_scores = [r["score_b"] for r in rows_ if r["score_b"] is not None]
        wins_a = sum(1 for r in rows_ if r["winner"] == "a")
        wins_b = sum(1 for r in rows_ if r["winner"] == "b")
        ties = sum(1 for r in rows_ if r["winner"] == "tie")
        n = len(rows_)
        return {
            "n": n,
            "mean_a": round(mean(a_scores), 1) if a_scores else None,
            "mean_b": round(mean(b_scores), 1) if b_scores else None,
            "delta": round(mean(b_scores) - mean(a_scores), 1) if a_scores and b_scores else None,
            "winrate_a": round(100 * wins_a / n, 0) if n else 0,
            "winrate_b": round(100 * wins_b / n, 0) if n else 0,
            "ties": round(100 * ties / n, 0) if n else 0,
            "wins_a_n": wins_a,
            "wins_b_n": wins_b,
            "ties_n": ties,
        }

    return {
        "run": str(run_dir.relative_to(DEFAULT_RUNS)),
        "total": len(rows),
        "ok": len(ok),
        "dsq_a": len(dsq_a),
        "dsq_b": len(dsq_b),
        "dsq_both": len(dsq_both),
        "headline_all": headline([r for r in rows if not r.get("error")]),
        "headline_clean": headline(ok),
        "dsq_idxs": {
            "a": [(r["idx"], r["a_reason"], r["len_a"]) for r in dsq_a],
            "b": [(r["idx"], r["b_reason"], r["len_b"]) for r in dsq_b],
            "both": [(r["idx"], r["a_reason"], r["b_reason"]) for r in dsq_both],
        },
    }


def find_finished_runs(base: Path) -> list[Path]:
    """Find run dirs that have ≥30 task-*.json files (so kimi's incomplete dir is excluded)."""
    out = []
    for d in base.rglob("task-01.json"):
        run_dir = d.parent
        if len(list(run_dir.glob("task-*.json"))) >= 30:
            out.append(run_dir)
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*", help="run dir(s); default: scan DEFAULT_RUNS")
    ap.add_argument("--rerun-list", type=Path, help="dump (run, task_idx, side) JSON list")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.runs:
        run_paths = [Path(r) for r in args.runs]
    else:
        run_paths = find_finished_runs(DEFAULT_RUNS)

    results = []
    rerun = []
    for rp in run_paths:
        r = audit_run(rp)
        results.append(r)
        if r.get("skip"):
            continue
        for idx, reason, _ in r["dsq_idxs"]["a"]:
            rerun.append({"run": r["run"], "task_idx": idx, "side": "a", "reason": reason})
        for idx, reason, _ in r["dsq_idxs"]["b"]:
            rerun.append({"run": r["run"], "task_idx": idx, "side": "b", "reason": reason})
        for idx, ra, rb in r["dsq_idxs"]["both"]:
            rerun.append({"run": r["run"], "task_idx": idx, "side": "both", "reason": f"{ra}|{rb}"})

    if args.rerun_list:
        args.rerun_list.write_text(json.dumps(rerun, indent=2))
        print(f"# wrote {len(rerun)} rerun entries → {args.rerun_list}", file=sys.stderr)

    # Print markdown summary
    print("# Bench audit — DSQ filter\n")
    print("| Run | N | OK | DSQ-A | DSQ-B | DSQ-both | Mean A all → clean | Mean B all → clean | Δ all → clean | Winrate B all → clean |")
    print("|---|---:|---:|---:|---:|---:|---|---|---|---|")
    for r in results:
        if r.get("skip"):
            continue
        h_all = r["headline_all"] or {}
        h_clean = r["headline_clean"] or {}

        def fmt_score(all_v, clean_v):
            if all_v is None and clean_v is None:
                return "—"
            return f"{all_v} → **{clean_v}**" if all_v != clean_v else f"{all_v}"

        ma = fmt_score(h_all.get("mean_a"), h_clean.get("mean_a"))
        mb = fmt_score(h_all.get("mean_b"), h_clean.get("mean_b"))
        d_ = fmt_score(h_all.get("delta"), h_clean.get("delta"))
        wb = fmt_score(h_all.get("winrate_b"), h_clean.get("winrate_b"))
        run_name = r["run"].split("/")[-1] or r["run"]
        print(
            f"| {run_name} | {r['total']} | {r['ok']} | {r['dsq_a']} | {r['dsq_b']} | {r['dsq_both']} "
            f"| {ma} | {mb} | {d_} | {wb}% |"
        )

    # Per-run detail of DSQ idxs
    if not args.quiet:
        print("\n## DSQ task list (for targeted rerun)\n")
        for r in results:
            if r.get("skip") or (r["dsq_a"] + r["dsq_b"] + r["dsq_both"] == 0):
                continue
            run_name = r["run"].split("/")[-1] or r["run"]
            print(f"### {run_name}")
            if r["dsq_idxs"]["a"]:
                print(f"- A-side fail: {r['dsq_idxs']['a']}")
            if r["dsq_idxs"]["b"]:
                print(f"- B-side fail: {r['dsq_idxs']['b']}")
            if r["dsq_idxs"]["both"]:
                print(f"- BOTH fail: {r['dsq_idxs']['both']}")


if __name__ == "__main__":
    main()
