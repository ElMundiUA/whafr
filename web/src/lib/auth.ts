// Auth0 SSR helpers. Stateless: we verify Auth0's ID token against
// the tenant's JWKS, then re-sign a tiny session JWT with a local
// HMAC secret and stash it in an httpOnly cookie. No server-side
// store, no Auth0 round-trip on every request.

import { createRemoteJWKSet, jwtVerify, SignJWT } from "jose";
import type { APIContext } from "astro";

const AUTH0_DOMAIN = import.meta.env.AUTH0_DOMAIN;
const AUTH0_CLIENT_ID = import.meta.env.AUTH0_CLIENT_ID;
const AUTH0_CLIENT_SECRET = import.meta.env.AUTH0_CLIENT_SECRET;
const AUTH0_AUDIENCE = import.meta.env.AUTH0_AUDIENCE;
const AUTH0_SCOPE = import.meta.env.AUTH0_SCOPE ?? "openid profile email";
const SESSION_SECRET = import.meta.env.SESSION_SECRET ?? "dev-only-change-me";
const ADMIN_EMAIL = import.meta.env.ADMIN_EMAIL ?? "";

export const SESSION_COOKIE = "lh_session";
export const SESSION_TTL_SECONDS = 60 * 60 * 24 * 7; // 7d

export interface SessionUser {
  sub: string;
  email: string;
  name?: string;
  picture?: string;
  tier: "anon" | "free" | "pro";
  is_admin: boolean;
}

const issuer = AUTH0_DOMAIN ? `https://${AUTH0_DOMAIN}/` : "";
const jwks = AUTH0_DOMAIN
  ? createRemoteJWKSet(new URL(`https://${AUTH0_DOMAIN}/.well-known/jwks.json`))
  : null;
const secretKey = new TextEncoder().encode(SESSION_SECRET);

export function isAuthConfigured(): boolean {
  return Boolean(AUTH0_DOMAIN && AUTH0_CLIENT_ID && AUTH0_CLIENT_SECRET);
}

export function buildAuthorizeUrl(state: string, redirectUri: string): string {
  const u = new URL(`https://${AUTH0_DOMAIN}/authorize`);
  u.searchParams.set("response_type", "code");
  u.searchParams.set("client_id", AUTH0_CLIENT_ID!);
  u.searchParams.set("redirect_uri", redirectUri);
  u.searchParams.set("scope", AUTH0_SCOPE);
  if (AUTH0_AUDIENCE) u.searchParams.set("audience", AUTH0_AUDIENCE);
  u.searchParams.set("state", state);
  return u.toString();
}

export async function exchangeCode(
  code: string,
  redirectUri: string,
): Promise<{ id_token: string; access_token: string }> {
  const r = await fetch(`https://${AUTH0_DOMAIN}/oauth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      grant_type: "authorization_code",
      client_id: AUTH0_CLIENT_ID,
      client_secret: AUTH0_CLIENT_SECRET,
      code,
      redirect_uri: redirectUri,
    }),
  });
  if (!r.ok) {
    throw new Error(`Auth0 token exchange failed: ${r.status} ${await r.text()}`);
  }
  return await r.json();
}

export async function verifyIdToken(idToken: string): Promise<SessionUser> {
  if (!jwks) throw new Error("Auth0 not configured");
  const { payload } = await jwtVerify(idToken, jwks, {
    issuer,
    audience: AUTH0_CLIENT_ID,
  });
  const email = String(payload.email ?? "");
  // Tier comes from a custom Auth0 Action claim. Defaults to "free"
  // if the user is signed in but no tier was attached yet.
  const tier =
    (payload["https://lighthouse.harborgang.com/tier"] as SessionUser["tier"]) ??
    "free";
  return {
    sub: String(payload.sub),
    email,
    name: payload.name as string | undefined,
    picture: payload.picture as string | undefined,
    tier,
    is_admin: ADMIN_EMAIL !== "" && email.toLowerCase() === ADMIN_EMAIL.toLowerCase(),
  };
}

export async function issueSessionCookie(user: SessionUser): Promise<string> {
  return await new SignJWT({ ...user })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime(`${SESSION_TTL_SECONDS}s`)
    .sign(secretKey);
}

export async function readSessionCookie(
  ctx: APIContext | { request: Request },
): Promise<SessionUser | null> {
  const cookieHeader = ctx.request.headers.get("cookie") ?? "";
  const match = cookieHeader.match(new RegExp(`${SESSION_COOKIE}=([^;]+)`));
  if (!match) return null;
  try {
    const { payload } = await jwtVerify(match[1], secretKey);
    return {
      sub: String(payload.sub),
      email: String(payload.email),
      name: payload.name as string | undefined,
      picture: payload.picture as string | undefined,
      tier: payload.tier as SessionUser["tier"],
      is_admin: Boolean(payload.is_admin),
    };
  } catch {
    return null;
  }
}

export function clearSessionCookieHeader(): string {
  return `${SESSION_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;
}

export function setSessionCookieHeader(token: string): string {
  return `${SESSION_COOKIE}=${token}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${SESSION_TTL_SECONDS}`;
}

export function logoutUrl(returnTo: string): string {
  const u = new URL(`https://${AUTH0_DOMAIN}/v2/logout`);
  u.searchParams.set("client_id", AUTH0_CLIENT_ID!);
  u.searchParams.set("returnTo", returnTo);
  return u.toString();
}
