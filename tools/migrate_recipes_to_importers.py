"""One-shot: import every YAML recipe under ``data/source-research/``
into the new ``importers`` table.

The legacy pipeline (k8s CronJobs running the YAML scheduler) stays
in charge of fan-out and scheduling — this script only copies the
config so the admin UI sees every crawl in one place. After migration:

- ``/admin/importers`` lists all 1k+ saved configs.
- Clicking "Run now" on any of them re-fires the same connector with
  the same args, writing into the same chunks table.
- Auto-scheduling stays with the existing CronJobs (no double ingest).

Idempotent: re-running skips importer rows whose ``name`` already
exists (the legacy YAML names are unique — they're the recipe slugs
stamped on the chunks).

Usage::

    LIGHTHOUSE_PG_URL=... LIGHTHOUSE_SECRETS_KEY=... \\
        python tools/migrate_recipes_to_importers.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import asyncpg
import yaml

logger = logging.getLogger("migrate_recipes")


# YAML files in data/source-research/ that aren't recipe dumps.
_NON_RECIPE_FILES = {
    "authority-registry.yaml",
    "popularity-index.yaml",
}


def _normalise_github(args: dict[str, Any]) -> dict[str, Any]:
    """Both `github` and `github_releases` YAMLs accept either
    ``slug: owner/repo`` or ``owner/repo`` split. The new schema
    requires explicit ``owner`` + ``repo`` fields."""
    slug = args.get("slug")
    out = dict(args)
    if slug and "/" in slug:
        out.pop("slug", None)
        owner, repo = slug.split("/", 1)
        out["owner"] = owner
        out["repo"] = repo
    return out


def _normalise_sitemap(args: dict[str, Any]) -> dict[str, Any]:
    """`include_paths` is a list in YAML but the importer schema
    expects newline-separated text (so the admin form can paste/edit
    a textarea)."""
    out = dict(args)
    if isinstance(out.get("include_paths"), list):
        out["include_paths"] = "\n".join(out["include_paths"])
    return out


def _normalise_url_list(args: dict[str, Any]) -> dict[str, Any]:
    """`urls` in YAML is a list; the importer accepts newline-text."""
    urls = args.get("urls") or []
    return {"urls": "\n".join(urls)}


def _normalise_rss(args: dict[str, Any]) -> dict[str, Any]:
    out = dict(args)
    feeds = out.get("feeds")
    if feeds is None and "url" in out:
        feeds = [out.pop("url")]
    if isinstance(feeds, list):
        out["feeds"] = "\n".join(feeds)
    return out


# Connector key in YAML → (importer type in registry, args normaliser).
# `github` YAMLs migrated to `github_repo` since GitHubTreeConnector is
# the resilient implementation for large repos (LlamaIndex's reader
# blew up on mdn/content etc).
_MAPPING: dict[str, tuple[str, Any]] = {
    "sitemap": ("sitemap", _normalise_sitemap),
    "web": ("url_list", _normalise_url_list),
    "github": ("github_repo", _normalise_github),
    "github_tree": ("github_repo", _normalise_github),
    "rss": ("rss", _normalise_rss),
    "github_releases": ("github_releases", _normalise_github),
}


def _split_secrets(
    importer_type: str, config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Pull declared secret keys out of the config dict — the runner
    expects them encrypted, not in plain config."""
    from lighthouse.importers.registry import lookup_importer

    cls = lookup_importer(importer_type)
    secret_keys = set(cls.meta.secret_keys)
    plain = {k: v for k, v in config.items() if k not in secret_keys}
    secrets = {
        k: str(config[k])
        for k in secret_keys
        if k in config and config[k] is not None and str(config[k]).strip()
    }
    return plain, secrets


def _load_yaml_recipes(root: Path) -> list[tuple[str, dict[str, Any]]]:
    """Walk every YAML in `root`, skip the non-recipe files, yield
    (recipe_role_slug, source_dict) tuples. The role slug = YAML
    stem (e.g. ``clarification.yaml`` → ``clarification``)."""
    out: list[tuple[str, dict[str, Any]]] = []
    for p in sorted(root.glob("*.yaml")):
        if p.name in _NON_RECIPE_FILES:
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.warning("skip %s: yaml parse failed: %s", p.name, exc)
            continue
        sources = data.get("sources")
        if not isinstance(sources, list):
            continue
        role = p.stem
        for src in sources:
            if not isinstance(src, dict) or "name" not in src or "connector" not in src:
                continue
            out.append((role, src))
    return out


def _strip_neon(url: str) -> str:
    """Mirror flat_graph: asyncpg rejects channel_binding etc."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    s = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(s.query) if k != "channel_binding"]
    return urlunsplit(s._replace(query=urlencode(q)))


async def _ensure_schema(conn: asyncpg.Connection, web_sql_root: Path) -> None:
    """Idempotently apply 002 + 003 migrations against the connection.

    Production already has these applied — this is defensive so the
    script can be re-run on a fresh dev DB or a self-hosted engine."""
    for fname in ("002_importers.sql", "003_importers_unique_name.sql"):
        await conn.execute((web_sql_root / fname).read_text())


async def _migrate(*, dry_run: bool) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    recipes = _load_yaml_recipes(repo_root / "data" / "source-research")
    logger.info("found %d sources in YAML recipes", len(recipes))

    # Build the importer-row payloads first so a parse failure aborts
    # before we touch the DB.
    payloads: list[tuple[str, str, str, dict[str, Any], dict[str, str]]] = []
    unknown_connectors: dict[str, int] = {}
    for role, src in recipes:
        kind = src["connector"]
        if kind not in _MAPPING:
            unknown_connectors[kind] = unknown_connectors.get(kind, 0) + 1
            continue
        importer_type, normaliser = _MAPPING[kind]
        raw_args = dict(src.get("args", {}) or {})
        norm_args = normaliser(raw_args)
        plain, secrets = _split_secrets(importer_type, norm_args)
        name = str(src["name"])
        description = f"Migrated from {role}.yaml on 2026-05-19"
        payloads.append((importer_type, name, description, plain, secrets))

    if unknown_connectors:
        logger.warning("unknown connectors skipped: %s", unknown_connectors)

    if dry_run:
        for type_, name, _, cfg, secrets in payloads[:5]:
            logger.info(
                "DRY %s %-40s cfg=%s secrets=%s",
                type_,
                name[:40],
                json.dumps(cfg)[:120],
                list(secrets),
            )
        logger.info(
            "DRY: would insert %d importer rows (showing first 5).",
            len(payloads),
        )
        return

    # Live import.
    url = os.environ.get("LIGHTHOUSE_PG_URL")
    if not url:
        sys.exit("LIGHTHOUSE_PG_URL not set")

    from lighthouse.importers import crypto

    conn = await asyncpg.connect(_strip_neon(url), statement_cache_size=0)
    try:
        await _ensure_schema(conn, repo_root / "web" / "sql")
        inserted = 0
        skipped = 0
        for importer_type, name, description, cfg, secrets in payloads:
            secrets_enc = (
                crypto.encrypt_secrets(secrets) if secrets else None
            )
            row = await conn.fetchrow(
                """
                INSERT INTO importers
                  (type, name, description, recipe, config, secrets_enc, created_by)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
                ON CONFLICT (name) DO NOTHING
                RETURNING id
                """,
                importer_type,
                name,
                description,
                name,  # legacy pipeline uses the spec name as the recipe slug
                json.dumps(cfg),
                secrets_enc,
                "migrate_recipes.py",
            )
            if row:
                inserted += 1
            else:
                skipped += 1
        logger.info(
            "Migration complete: %d inserted, %d already-existed (skipped)",
            inserted,
            skipped,
        )
    finally:
        await conn.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse + map but don't write to the DB.",
    )
    args = parser.parse_args()
    asyncio.run(_migrate(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
