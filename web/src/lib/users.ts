// User row upserts. Called from the auth callback (so a sign-in
// always produces or refreshes a row) and from the Paddle webhook
// (which sets tier/subscription_id).

import { one, query } from "@/lib/db";
import type { SessionUser } from "@/lib/auth";

interface UserRow {
  id: number;
  auth0_sub: string;
  email: string;
  tier: "free" | "pro";
  pro_until: Date | null;
  paddle_customer_id: string | null;
  paddle_subscription_id: string | null;
}

export async function upsertFromAuth(user: SessionUser): Promise<UserRow> {
  const row = await one<UserRow>(
    `INSERT INTO users (auth0_sub, email, name, picture, last_login_at)
     VALUES ($1, $2, $3, $4, NOW())
     ON CONFLICT (auth0_sub) DO UPDATE
       SET email = EXCLUDED.email,
           name = EXCLUDED.name,
           picture = EXCLUDED.picture,
           last_login_at = NOW()
     RETURNING id, auth0_sub, email, tier, pro_until,
               paddle_customer_id, paddle_subscription_id`,
    [user.sub, user.email, user.name ?? null, user.picture ?? null],
  );
  if (!row) throw new Error("upsert returned no row");
  return row;
}

export async function effectiveTier(user: SessionUser | null): Promise<"anon" | "free" | "pro"> {
  if (!user) return "anon";
  // Source of truth for tier is the DB row (custom claim can lag).
  const row = await one<{ tier: "free" | "pro"; pro_until: Date | null }>(
    `SELECT tier, pro_until FROM users WHERE auth0_sub = $1`,
    [user.sub],
  );
  if (!row) return "free";
  if (row.tier === "pro") {
    if (!row.pro_until || row.pro_until > new Date()) return "pro";
    return "free";  // expired
  }
  return "free";
}

export async function listUsers(limit = 100): Promise<UserRow[]> {
  return await query<UserRow>(
    `SELECT id, auth0_sub, email, tier, pro_until,
            paddle_customer_id, paddle_subscription_id
     FROM users
     ORDER BY last_login_at DESC NULLS LAST
     LIMIT $1`,
    [limit],
  );
}
