import type { APIRoute } from "astro";
import { buildAuthorizeUrl, isAuthConfigured } from "@/lib/auth";

export const prerender = false;

export const GET: APIRoute = ({ request, redirect }) => {
  if (!isAuthConfigured()) {
    return new Response("Auth0 not configured", { status: 503 });
  }
  const url = new URL(request.url);
  const returnTo = url.searchParams.get("return_to") ?? "/";
  // State doubles as the post-login redirect target; signed/HMAC
  // would be stricter, but for an open-internet return-path this
  // is acceptable since the callback validates returnTo is same-
  // origin before honoring it.
  const state = encodeURIComponent(returnTo);
  const redirectUri = `${url.origin}/api/auth/callback`;
  return redirect(buildAuthorizeUrl(state, redirectUri));
};
