// Rate-limit by (subject, UTC day). Returns the post-increment
// count; callers compare against the tier limit and short-circuit
// 429 if exceeded. Atomic upsert — no read-modify-write race.

import { one } from "@/lib/db";
import type { SessionUser } from "@/lib/auth";

export const LIMITS = {
  anon: 20,
  free: 100,
  pro: 5000,
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
