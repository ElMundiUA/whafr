// PATCH / DELETE for one importer. Admin-gated, proxies to Python.

import type { APIRoute } from "astro";
import { deleteImporter, patchImporter } from "@/lib/importers-api";

export const prerender = false;

function deny(locals: App.Locals): Response | null {
  if (!locals.user || !locals.user.is_admin) {
    return new Response(JSON.stringify({ error: "forbidden" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });
  }
  return null;
}

export const PATCH: APIRoute = async ({ locals, params, request }) => {
  const d = deny(locals);
  if (d) return d;
  const id = params.id;
  if (!id) return new Response("missing id", { status: 400 });
  let body: Parameters<typeof patchImporter>[1];
  try {
    body = await request.json();
  } catch {
    return new Response("invalid JSON", { status: 400 });
  }
  try {
    const r = await patchImporter(id, body);
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

export const DELETE: APIRoute = async ({ locals, params }) => {
  const d = deny(locals);
  if (d) return d;
  const id = params.id;
  if (!id) return new Response("missing id", { status: 400 });
  try {
    await deleteImporter(id);
    return new Response(null, { status: 204 });
  } catch (err) {
    return new Response(JSON.stringify({ error: String(err) }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
};
