"""One-shot consolidation: dissolve gaps-* recipes back into the
per-role recipe yamls and append missing-library snippets from the
popularity index. Single source of truth = role-recipes/<role>.yaml.

Steps:
  1. Read every gaps-*.yaml in role-recipes/. Group its sources by
     the per-source ``role`` field and append to the corresponding
     role recipe under ``tier1_mainstream`` (creating it if absent).
  2. Read popularity-index.yaml and append source-snippets for
     every entry whose status is missing/thin (we can't tell here,
     but we add every entry with a source snippet and let the
     ingest's delta-skip ignore what's already there).
  3. Delete the gaps-*.yaml files (both role-recipes/ + compiled/).
  4. Print a per-role summary of how many sources were added.

After this:
  - Run `tools/role_recipe_compose.py`
  - Re-apply the ingest cronjobs for affected roles
  - Trigger one manual run per affected role to backfill
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import yaml

RECIPE_DIR = Path("data/source-research/role-recipes")
COMPILED_DIR = Path("data/source-research/compiled")
POP_INDEX = Path("data/source-research/popularity-index.yaml")


def _load(p: Path) -> dict:
    with p.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _dump(p: Path, data: dict) -> None:
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False, indent=2)


def _existing_source_names(recipe: dict) -> set[str]:
    names: set[str] = set()
    for key in (
        "tier1_mainstream",
        "tier2_canonical",
        "tier3_methodology",
        "tier4_streams",
    ):
        for s in (recipe.get(key) or []):
            n = s.get("name") or s.get("id")
            if n:
                names.add(n)
    return names


def _append_to_tier(recipe: dict, tier_key: str, src: dict) -> None:
    bucket = recipe.setdefault(tier_key, [])
    bucket.append(src)


def main() -> int:
    additions_per_role: dict[str, int] = defaultdict(int)
    # Cache loaded role recipes so we modify them in-memory then
    # write back once each.
    recipes: dict[str, dict] = {}

    def _recipe(role: str) -> dict | None:
        if role in recipes:
            return recipes[role]
        path = RECIPE_DIR / f"{role}.yaml"
        if not path.exists():
            print(f"  ! no recipe for role={role} — skipping", file=sys.stderr)
            return None
        recipes[role] = _load(path)
        return recipes[role]

    # ---- 1. Dissolve gaps-* into per-role recipes ----
    print("== dissolving gaps-* role-recipes ==")
    gaps_paths = sorted(RECIPE_DIR.glob("gaps-*.yaml"))
    for gp in gaps_paths:
        g = _load(gp)
        srcs = g.get("sources") or []
        print(f"  {gp.name}: {len(srcs)} sources")
        for s in srcs:
            role = s.get("role")
            if not role:
                print(f"    ! source {s.get('name')} has no role — skip")
                continue
            rec = _recipe(role)
            if rec is None:
                continue
            existing = _existing_source_names(rec)
            name = s.get("name")
            if name in existing:
                continue
            # Drop the per-source role field; compose tool infers
            # role from the recipe filename.
            clean = {k: v for k, v in s.items() if k != "role"}
            _append_to_tier(rec, "tier1_mainstream", clean)
            additions_per_role[role] += 1

    # ---- 2. Append popularity-index missing entries ----
    print("== appending popularity-index sources ==")
    pop = _load(POP_INDEX)
    for lib in (pop.get("libraries") or []):
        if not lib.get("source"):
            continue
        role = lib.get("role")
        rec = _recipe(role) if role else None
        if rec is None:
            continue
        existing = _existing_source_names(rec)
        src = lib["source"]
        name = src.get("name")
        if name in existing:
            continue
        # Add a schedule if missing — match the per-role default.
        src.setdefault("schedule", {"every": "30d"})
        # Tier 1 mainstream is the right bucket for "popular library
        # docs" — the rest is canonical standards / methodology /
        # streams which these aren't.
        _append_to_tier(rec, "tier1_mainstream", src)
        additions_per_role[role] += 1

    # ---- Write back ----
    for role, rec in recipes.items():
        _dump(RECIPE_DIR / f"{role}.yaml", rec)

    # ---- Delete gaps-* files ----
    print("== removing dissolved gaps-* ==")
    for gp in gaps_paths:
        gp.unlink()
        print(f"  rm {gp.name}")
        compiled = COMPILED_DIR / gp.name
        if compiled.exists():
            compiled.unlink()
            print(f"  rm compiled/{compiled.name}")

    print("== additions per role ==")
    for role, n in sorted(additions_per_role.items(), key=lambda x: -x[1]):
        print(f"  +{n:>3}  {role}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
