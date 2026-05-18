// Corpus stats for the home page. Read-only against the live DB.
// Cached behind lib/cache so we don't hammer Neon on every visit.

import { one, query } from "@/lib/db";
import { cached } from "@/lib/cache";

export interface CorpusStats {
  total_chunks: number;
  total_sources: number;
  total_recipes: number;
}

export interface RecentRepo {
  repo: string;          // owner/repo (github_tree) or domain (url)
  recipes: string[];     // which role recipes claim it
  chunks: number;
  last_ingest: Date | null;
}

export async function corpusStats(): Promise<CorpusStats> {
  return cached("stats:corpus:v1", 300, async () => {
    const row = await one<{ chunks: string; sources: string }>(
      `SELECT COUNT(*)::text AS chunks,
              COUNT(DISTINCT source)::text AS sources
         FROM chunks`,
    );
    const recipeRow = await one<{ n: string }>(
      `SELECT COUNT(DISTINCT r)::text AS n
         FROM (SELECT unnest(recipes) AS r FROM chunks) t`,
    );
    return {
      total_chunks: Number(row?.chunks ?? 0),
      total_sources: Number(row?.sources ?? 0),
      total_recipes: Number(recipeRow?.n ?? 0),
    };
  });
}

// Most-recently-ingested upstream repos / domains. Used to show
// freshness on the home page. Grouped by the "owner/repo" segment
// for github_tree sources and by the URL host for plain web URLs.
export async function recentRepos(limit = 8): Promise<RecentRepo[]> {
  return cached(`stats:recent:v1:${limit}`, 120, async () => {
    return await query<RecentRepo>(
      `WITH grouped AS (
         SELECT
           CASE
             WHEN source LIKE 'github-tree:%' THEN
               split_part(split_part(source, ':', 2), '@', 1)
             WHEN source ~* '^https?://' THEN
               substring(source FROM 'https?://([^/]+)')
             ELSE source
           END AS repo,
           recipes,
           ingested_at
           FROM chunks
       )
       SELECT
         repo,
         (SELECT COALESCE(ARRAY_AGG(DISTINCT r), ARRAY[]::TEXT[])
            FROM (SELECT unnest(recipes) AS r FROM grouped g2
                  WHERE g2.repo = grouped.repo) s)         AS recipes,
         COUNT(*)::int                                     AS chunks,
         MAX(ingested_at)                                  AS last_ingest
         FROM grouped
        GROUP BY repo
        ORDER BY last_ingest DESC NULLS LAST
        LIMIT $1`,
      [limit],
    );
  });
}

// Anonymised activity feed for the home page "live pulse" widget.
// Returns up to N most-recent ingest events as
// {chunks, recipe, minutes_ago} triples — no upstream URLs, no repo
// names. The repo field is stripped server-side; only the recipe
// slug and the chunk count leave the server.
export interface IndexedEvent {
  chunks: number;
  recipe: string;
  minutes_ago: number;
}

export async function recentEvents(limit = 30): Promise<IndexedEvent[]> {
  const rows = await recentRepos(limit);
  const now = Date.now();
  return rows
    .map((r) => {
      const recipe = (r.recipes ?? [])[0] ?? "";
      const ts = r.last_ingest ? new Date(r.last_ingest).getTime() : now;
      const minutes = Math.max(0, Math.round((now - ts) / 60_000));
      return { chunks: r.chunks, recipe, minutes_ago: minutes };
    })
    .filter((e) => e.recipe.length > 0);
}

// Pretty "5 min ago" / "2 hours ago" / "3 days ago".
export function timeAgo(d: Date | null): string {
  if (!d) return "—";
  const ms = Date.now() - new Date(d).getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const hr = Math.round(min / 60);
  if (hr < 48) return `${hr} h ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day} d ago`;
  const mo = Math.round(day / 30);
  return `${mo} mo ago`;
}
