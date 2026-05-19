// POST /api/admin/importers/discover → proxies to Python which probes
// the source with provided creds and returns selectable items. Admin-
// gated; secrets travel in the request body, never persisted client
// side. Used by the new-importer wizard between the auth step and
// the multi-select step.

import type { APIRoute } from "astro";
import { discover } from "@/lib/importers-api";

export const prerender = false;

export const POST: APIRoute = async ({ locals, request }) => {
  if (!locals.user || !locals.user.is_admin) {
    return new Response(JSON.stringify({ error: "forbidden" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });
  }
  let body: Parameters<typeof discover>[0];
  try {
    body = await request.json();
  } catch {
    return new Response("invalid JSON", { status: 400 });
  }
  try {
    const r = await discover(body);
    return new Response(JSON.stringify(r), {
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    return new Response(JSON.stringify({ error: String(err) }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
};
