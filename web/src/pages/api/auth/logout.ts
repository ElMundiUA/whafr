import type { APIRoute } from "astro";
import { clearSessionCookieHeader, logoutUrl } from "@/lib/auth";
import { publicOrigin } from "@/lib/origin";

export const prerender = false;

export const GET: APIRoute = ({ request }) => {
  return new Response(null, {
    status: 302,
    headers: {
      Location: logoutUrl(publicOrigin(request)),
      "Set-Cookie": clearSessionCookieHeader(),
    },
  });
};
