import type { APIRoute } from "astro";

export const prerender = false;

// Lightweight liveness/readiness endpoint — no fetches, no DB
// queries, no auth. Just "I'm up." k8s probes hit this instead of
// `/`, which SSR-fetches six teasers from the API and can exceed
// the default probe timeout.
export const GET: APIRoute = () => {
  return new Response("ok", {
    status: 200,
    headers: { "Content-Type": "text/plain" },
  });
};
