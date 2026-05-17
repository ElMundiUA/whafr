import type { APIRoute } from "astro";
import { clearSessionCookieHeader, logoutUrl } from "@/lib/auth";

export const prerender = false;

export const GET: APIRoute = ({ request }) => {
  const origin = new URL(request.url).origin;
  return new Response(null, {
    status: 302,
    headers: {
      Location: logoutUrl(origin),
      "Set-Cookie": clearSessionCookieHeader(),
    },
  });
};
