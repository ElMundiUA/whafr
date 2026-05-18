// Server-side helpers backing the /admin routes. Every function
// here assumes the caller has already verified `user.is_admin`.

import { query, one } from "@/lib/db";

export interface SourceStat {
  source: string;
  chunks: number;
  last_ingest: Date | null;
  earliest_published: Date | null;
}

export async function corpusOverview(): Promise<{
  total_chunks: number;
  total_sources: number;
  with_summary: number;
  with_keywords: number;
  with_embedding: number;
}> {
  const row = await one<{
    total: string;
    sources: string;
    summary: string;
    keywords: string;
    embedding: string;
  }>(
    `SELECT
        COUNT(*) AS total,
        COUNT(DISTINCT source) AS sources,
        COUNT(*) FILTER (WHERE COALESCE(summary, '') <> '') AS summary,
        COUNT(*) FILTER (WHERE COALESCE(keywords, '') <> '') AS keywords,
        COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS embedding
       FROM chunks`,
  );
  return {
    total_chunks: Number(row?.total ?? 0),
    total_sources: Number(row?.sources ?? 0),
    with_summary: Number(row?.summary ?? 0),
    with_keywords: Number(row?.keywords ?? 0),
    with_embedding: Number(row?.embedding ?? 0),
  };
}

export async function topSources(limit = 30): Promise<SourceStat[]> {
  return await query<SourceStat>(
    `SELECT
        source,
        COUNT(*)::int AS chunks,
        MAX(ingested_at) AS last_ingest,
        MIN(published_at) AS earliest_published
       FROM chunks
       GROUP BY source
       ORDER BY chunks DESC
       LIMIT $1`,
    [limit],
  );
}

export async function recentUsers(limit = 50): Promise<{
  id: number;
  email: string;
  tier: string;
  last_login_at: Date | null;
  pro_until: Date | null;
}[]> {
  return await query(
    `SELECT id, email, tier, last_login_at, pro_until
       FROM users
       ORDER BY last_login_at DESC NULLS LAST
       LIMIT $1`,
    [limit],
  );
}

export async function recentPaddleEvents(limit = 30): Promise<{
  event_id: string;
  event_type: string;
  processed_at: Date | null;
  received_at: Date;
}[]> {
  return await query(
    `SELECT event_id, event_type, processed_at, received_at
       FROM paddle_events
       ORDER BY received_at DESC
       LIMIT $1`,
    [limit],
  );
}
