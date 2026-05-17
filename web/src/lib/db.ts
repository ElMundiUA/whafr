// Pool against the same Neon database that holds the corpus. The
// connection string is set at deploy time (LIGHTHOUSE_DATABASE_URL,
// shared with the Python API).

import pg from "pg";

const conn = import.meta.env.LIGHTHOUSE_DATABASE_URL ?? process.env.LIGHTHOUSE_DATABASE_URL;

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
