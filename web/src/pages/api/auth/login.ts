import type { APIRoute } from "astro";
import { buildAuthorizeUrl, isAuthConfigured } from "@/lib/auth";
import { publicOrigin } from "@/lib/origin";

export const prerender = false;

export const GET: APIRoute = ({ request, redirect }) => {
  if (!isAuthConfigured()) {
    return new Response("Auth0 not configured", { status: 503 });
  }
  const url = new URL(request.url);
  const returnTo = url.searchParams.get("return_to") ?? "/";
  const state = encodeURIComponent(returnTo);
  const redirectUri = `${publicOrigin(request)}/api/auth/callback`;
  return redirect(buildAuthorizeUrl(state, redirectUri));
};
