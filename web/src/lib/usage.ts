// Rate-limit by (subject, UTC day). Returns the post-increment
// count; callers compare against the tier limit and short-circuit
// 429 if exceeded. Atomic upsert — no read-modify-write race.

import { one } from "@/lib/db";
import type { SessionUser } from "@/lib/auth";

// Daily search caps. Numbers tuned for "200 reads as obviously
// sufficient" on Free and "$12 Pro under the expense-it-without-
// asking line" on Pro. Anonymous gets enough to evaluate before
// signing up but bites quickly on a real coding session.
export const LIMITS = {
  anon: 30,
  free: 200,
  pro: 1500,
} as const;

export type Tier = keyof typeof LIMITS;

export function tierForUser(user: SessionUser | null): Tier {
  if (!user) return "anon";
  return user.tier === "pro" ? "pro" : "free";
}

export function subjectFor(user: SessionUser | null, ip: string): string {
  return user ? `u:${user.sub}` : `ip:${ip}`;
}

interface UsageRow {
  count: number;
}

// Read-only counter — for the masthead "X of Y used today" chip.
// Doesn't bump. Returns 0 on lookup error so the chip never blocks
// the layout.
export async function currentUsage(subject: string): Promise<number> {
  try {
    const row = await one<{ count: number }>(
      `SELECT count FROM usage_daily
        WHERE subject = $1 AND day = CURRENT_DATE`,
      [subject],
    );
    return row?.count ?? 0;
  } catch {
    return 0;
  }
}

export async function bumpAndCheck(subject: string, limit: number): Promise<{
  count: number;
  allowed: boolean;
  remaining: number;
}> {
  // ON CONFLICT … RETURNING count gives us the post-increment
  // value in a single round-trip.
  const row = await one<UsageRow>(
    `INSERT INTO usage_daily (subject, day, count)
     VALUES ($1, CURRENT_DATE, 1)
     ON CONFLICT (subject, day)
     DO UPDATE SET count = usage_daily.count + 1
     RETURNING count`,
    [subject],
  );
  const count = row?.count ?? 1;
  return {
    count,
    allowed: count <= limit,
    remaining: Math.max(0, limit - count),
  };
}

export function clientIp(request: Request): string {
  // Prefer Cloudflare/Fastly/whatever the cluster's ingress puts at
  // the front; fall back to the direct remote addr if available.
  const cf = request.headers.get("cf-connecting-ip");
  if (cf) return cf;
  const fwd = request.headers.get("x-forwarded-for");
  if (fwd) return fwd.split(",")[0].trim();
  return request.headers.get("x-real-ip") ?? "unknown";
}
