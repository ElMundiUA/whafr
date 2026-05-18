// Pool against the same Neon database that holds the corpus. The
// connection string is set at deploy time (LIGHTHOUSE_DATABASE_URL,
// shared with the Python API).

import pg from "pg";

// Accept either env name — LIGHTHOUSE_DATABASE_URL is the web's
// preferred key, but the cluster's lighthouse-env Secret already
// publishes LIGHTHOUSE_PG_URL for the Python API to consume.
// Falling back means one Secret can drive both.
const conn =
  import.meta.env.LIGHTHOUSE_DATABASE_URL ||
  import.meta.env.LIGHTHOUSE_PG_URL ||
  process.env.LIGHTHOUSE_DATABASE_URL ||
  process.env.LIGHTHOUSE_PG_URL;

// Reuse a single pool across requests. With Neon's pooler endpoint
// the pool size matters less, but cap at 5 to avoid surprising
// concurrent-connection bills.
export const pool = conn
  ? new pg.Pool({ connectionString: conn, max: 5 })
  : null;

export async function query<T = unknown>(
  sql: string,
  params: unknown[] = [],
): Promise<T[]> {
  if (!pool) throw new Error("LIGHTHOUSE_DATABASE_URL not set");
  const r = await pool.query(sql, params);
  return r.rows as T[];
}

export async function one<T = unknown>(
  sql: string,
  params: unknown[] = [],
): Promise<T | null> {
  const rows = await query<T>(sql, params);
  return rows[0] ?? null;
}
