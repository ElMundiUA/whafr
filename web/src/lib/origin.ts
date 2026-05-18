// Astro's @astrojs/node adapter doesn't translate x-forwarded-*
// headers into request.url by default — behind an nginx ingress
// the URL the app sees has scheme/host of the LOCAL listener
// (http://localhost:4321), not the public origin. That breaks
// anywhere we build absolute URLs (OAuth callbacks, Paddle return
// URLs, share links). Resolve the public origin from forwarded
// headers explicitly.

export function publicOrigin(request: Request): string {
  const h = request.headers;
  const proto = (h.get("x-forwarded-proto") ?? "").split(",")[0].trim()
    || new URL(request.url).protocol.replace(":", "");
  const host =
    (h.get("x-forwarded-host") ?? "").split(",")[0].trim() ||
    h.get("host") ||
    new URL(request.url).host;
  return `${proto}://${host}`;
}
