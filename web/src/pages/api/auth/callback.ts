import type { APIRoute } from "astro";
import {
  exchangeCode,
  verifyIdToken,
  issueSessionCookie,
  setSessionCookieHeader,
} from "@/lib/auth";
import { upsertFromAuth, effectiveTier } from "@/lib/users";
import { publicOrigin } from "@/lib/origin";

export const prerender = false;

export const GET: APIRoute = async ({ request }) => {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state") ?? "";
  if (!code) return new Response("missing code", { status: 400 });

  const redirectUri = `${publicOrigin(request)}/api/auth/callback`;
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

  // returnTo must be same-origin to defeat open-redirect. If the
  // login was kicked off without an explicit destination (state
  // encodes "/"), drop the user on /home so they see MCP setup +
  // tier status instead of the public front page.
  let returnTo = "/home";
  try {
    const decoded = decodeURIComponent(state);
    if (decoded.startsWith("/") && !decoded.startsWith("//") && decoded !== "/") {
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
