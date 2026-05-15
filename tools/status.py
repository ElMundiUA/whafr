"""Lighthouse ingest dashboard.

One-shot status snapshot — reads the runner log, the unparseable
JSONL, and the FalkorDB graph, and prints a compact human-readable
summary of where we are.

Usage:
    python tools/status.py /tmp/ingest-sitemap-v4.log
    python tools/status.py /tmp/ingest-sitemap-v4.log --config data/source-research/sources.yaml
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path


# ---------- log parsing ---------------------------------------------------


_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3})")


def parse_ts(line: str) -> datetime | None:
    m = _TS.match(line)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")


def parse_log(path: Path) -> dict:
    """Walk the log once and pull out everything we need."""
    started_at: datetime | None = None
    last_ts: datetime | None = None
    fired: list[str] = []           # source names in order they started
    done_counts: dict[str, int] = {}  # source -> docs ingested (from "done — N documents from X")
    ingested_recent: list[datetime] = []  # last N ingested timestamps for rate calc

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            ts = parse_ts(line)
            if ts is None:
                continue
            if started_at is None:
                started_at = ts
            last_ts = ts

            # "firing source: NAME"
            m = re.search(r"firing source: (\S+)", line)
            if m:
                fired.append(m.group(1))
                continue

            # "done — N documents ingested from PREFIX (gate skipped M)"
            m = re.search(
                r"done — (\d+) documents? ingested from (\S+)", line
            )
            if m:
                n = int(m.group(1))
                prefix = m.group(2).rstrip(",")
                done_counts[prefix] = n
                continue

            # "ingested: TITLE"  — track for rate calc
            if "INFO lighthouse.ingest | ingested:" in line:
                ingested_recent.append(ts)

    return {
        "started_at": started_at,
        "last_ts": last_ts,
        "fired": fired,
        "done_counts": done_counts,
        "ingested_recent": ingested_recent,
    }


# ---------- helpers --------------------------------------------------------


def graph_counts() -> dict[str, int]:
    """Pull node/edge counts from FalkorDB via redis-cli."""
    out: dict[str, int] = {}
    for label in ("Episodic", "Entity"):
        try:
            res = subprocess.run(
                [
                    "docker",
                    "exec",
                    "lighthouse-falkordb",
                    "redis-cli",
                    "GRAPH.QUERY",
                    "lighthouse",
                    f'MATCH (n:{label}) RETURN count(n)',
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for ln in res.stdout.splitlines():
                if ln.strip().isdigit():
                    out[label] = int(ln.strip())
                    break
        except Exception:
            out[label] = -1
    for rel in ("RELATES_TO", "MENTIONS"):
        try:
            res = subprocess.run(
                [
                    "docker",
                    "exec",
                    "lighthouse-falkordb",
                    "redis-cli",
                    "GRAPH.QUERY",
                    "lighthouse",
                    f'MATCH ()-[r:{rel}]->() RETURN count(r)',
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for ln in res.stdout.splitlines():
                if ln.strip().isdigit():
                    out[rel] = int(ln.strip())
                    break
        except Exception:
            out[rel] = -1
    return out


def unparseable_breakdown(path: Path) -> Counter:
    if not path.exists():
        return Counter()
    c: Counter = Counter()
    for line in path.open():
        try:
            r = json.loads(line)
            c[r["reason"]] += 1
        except Exception:
            continue
    return c


def count_sources(config: Path) -> int:
    try:
        import yaml

        data = yaml.safe_load(config.read_text())
        return len((data or {}).get("sources") or [])
    except Exception:
        return 0


def fmt_dur(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def bar(pct: float, width: int = 24) -> str:
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


# ---------- main -----------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path, help="Path to runner log file")
    ap.add_argument(
        "--config",
        type=Path,
        default=Path("data/source-research/sources.yaml"),
        help="Sources YAML to read expected total from",
    )
    ap.add_argument(
        "--unparseable",
        type=Path,
        default=Path("data/source-research/unparseable.jsonl"),
    )
    args = ap.parse_args()

    if not args.log.exists():
        print(f"log not found: {args.log}", file=sys.stderr)
        return 1

    parsed = parse_log(args.log)
    total = count_sources(args.config)
    fired = parsed["fired"]
    done = parsed["done_counts"]
    fired_set = set(fired)
    done_set = set(done.keys())
    in_flight = fired_set - done_set

    now = datetime.now()
    started = parsed["started_at"]
    last = parsed["last_ts"]
    elapsed = (now - started).total_seconds() if started else 0
    silent_for = (now - last).total_seconds() if last else 0

    # Rate over last 5 min
    ingested_times = parsed["ingested_recent"]
    five_min_ago = now - timedelta(minutes=5)
    recent_ingests = [t for t in ingested_times if t >= five_min_ago]
    rate_5min = len(recent_ingests) / 5 if recent_ingests else 0  # docs/min
    total_ingested = len(ingested_times)
    overall_rate = total_ingested / max(1, elapsed / 60)

    # ETA: take the rate of source-completions per minute and project
    # the remaining count. Parallelism (max_concurrent) is implicit in
    # this rate — we observe ``done`` as it happens, so the rate
    # already reflects how many slots are working.
    remaining = total - len(done_set)
    elapsed_min = max(1 / 60, elapsed / 60)
    sources_per_min = len(done_set) / elapsed_min
    if sources_per_min > 0:
        eta_seconds = remaining / sources_per_min * 60
    else:
        eta_seconds = 0
    pct = (len(done_set) / total * 100) if total else 0

    gc = graph_counts()
    unparse = unparseable_breakdown(args.unparseable)
    unparse_total = sum(unparse.values())

    # ---------- render ----------
    line = "═" * 64
    print(line)
    print(f"  Lighthouse Ingest Dashboard         {now:%Y-%m-%d %H:%M:%S}")
    print(line)
    print()
    print(f"  Progress      {bar(pct)}  {pct:5.1f}%")
    print(f"  Sources       {len(done_set):>4d} done / {len(in_flight):>3d} in flight / {total:>3d} total")
    print(f"  Elapsed       {fmt_dur(elapsed):<12}  silent for {fmt_dur(silent_for)}")
    print(f"  Rate          {rate_5min:.1f} docs/min (5-min)  ·  {overall_rate:.1f} docs/min (avg)")
    if remaining > 0 and eta_seconds > 0:
        print(f"  ETA           ~{fmt_dur(eta_seconds)} remaining ({sources_per_min:.1f} sources/min)")
    print()
    print("  Graph state")
    print(f"    Episodes        {gc.get('Episodic', '?'):>6}")
    print(f"    Entities        {gc.get('Entity', '?'):>6}")
    print(f"    Facts (RELATES) {gc.get('RELATES_TO', '?'):>6}")
    print(f"    Mentions edges  {gc.get('MENTIONS', '?'):>6}")
    print()
    print(f"  Pipeline ({total_ingested + unparse_total} URLs processed)")
    print(f"    Ingested        {total_ingested:>6}")
    print(f"    Unparseable     {unparse_total:>6}")
    for reason, n in unparse.most_common():
        print(f"      └─ {reason:<22} {n:>5}")
    print()

    # Show 3 most-recent done sources
    if done:
        print("  Recent done sources (sample, last 5)")
        for prefix, n in list(done.items())[-5:]:
            print(f"    ✓ {prefix:<46} {n:>3} docs")
        print()

    # Show in-flight sources (still working)
    if in_flight:
        print(f"  Currently in flight ({len(in_flight)} sources)")
        for s in list(in_flight)[:8]:
            print(f"    … {s}")
        if len(in_flight) > 8:
            print(f"    … (+{len(in_flight) - 8} more)")
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
