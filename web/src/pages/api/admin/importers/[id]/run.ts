// POST /api/admin/importers/{id}/run → fires a background run via
// the Python admin router. Returns immediately; the detail page
// polls /admin/importers/{id} (which exposes status + last_run_at)
// to surface progress.

import type { APIRoute } from "astro";
import { runImporter } from "@/lib/importers-api";

export const prerender = false;

export const POST: APIRoute = async ({ locals, params }) => {
  if (!locals.user || !locals.user.is_admin) {
    return new Response(JSON.stringify({ error: "forbidden" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });
  }
  const id = params.id;
  if (!id) return new Response("missing id", { status: 400 });
  try {
    const r = await runImporter(id);
    return new Response(JSON.stringify(r), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    return new Response(JSON.stringify({ error: String(err) }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
};
