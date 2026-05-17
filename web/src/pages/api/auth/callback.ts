import type { APIRoute } from "astro";
import {
  exchangeCode,
  verifyIdToken,
  issueSessionCookie,
  setSessionCookieHeader,
} from "@/lib/auth";
import { upsertFromAuth, effectiveTier } from "@/lib/users";

export const prerender = false;

export const GET: APIRoute = async ({ request }) => {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state") ?? "";
  if (!code) return new Response("missing code", { status: 400 });

  const redirectUri = `${url.origin}/api/auth/callback`;
  const tokens = await exchangeCode(code, redirectUri);
  const user = await verifyIdToken(tokens.id_token);

  // Upsert + read DB-side tier so a Paddle-billed Pro who logs
  // back in immediately gets the right cookie (Auth0 custom claim
  // can lag the webhook by a request or two).
  try {
    await upsertFromAuth(user);
    const dbTier = await effectiveTier(user);
    if (dbTier === "pro" || dbTier === "free") user.tier = dbTier;
  } catch (err) {
    console.error("user upsert failed; falling back to claim-only tier", err);
  }
  const session = await issueSessionCookie(user);

  // returnTo must be same-origin to defeat open-redirect; default
  // to "/" otherwise.
  let returnTo = "/";
  try {
    const decoded = decodeURIComponent(state);
    if (decoded.startsWith("/") && !decoded.startsWith("//")) {
      returnTo = decoded;
    }
  } catch {
    /* keep default */
  }

  return new Response(null, {
    status: 302,
    headers: {
      Location: returnTo,
      "Set-Cookie": setSessionCookieHeader(session),
    },
  });
};
