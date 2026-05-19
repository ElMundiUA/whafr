// Astro API proxy: list / create importers via the Python admin
// router. Same admin-gate every /admin page uses — we re-check on
// each call instead of trusting only the page redirect, so a CSRF
// or stale-tab POST can't bypass.

import type { APIRoute } from "astro";
import { createImporter, listImporters } from "@/lib/importers-api";

export const prerender = false;

function requireAdmin(locals: App.Locals): Response | null {
  if (!locals.user || !locals.user.is_admin) {
    return new Response(JSON.stringify({ error: "forbidden" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });
  }
  return null;
}

export const GET: APIRoute = async ({ locals }) => {
  const denied = requireAdmin(locals);
  if (denied) return denied;
  try {
    const rows = await listImporters();
    return new Response(JSON.stringify(rows), {
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    return new Response(JSON.stringify({ error: String(err) }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
};

export const POST: APIRoute = async ({ locals, request }) => {
  const denied = requireAdmin(locals);
  if (denied) return denied;
  let body: Parameters<typeof createImporter>[0];
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: "invalid JSON" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }
  try {
    const created = await createImporter(body);
    return new Response(JSON.stringify(created), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    return new Response(JSON.stringify({ error: String(err) }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
};
