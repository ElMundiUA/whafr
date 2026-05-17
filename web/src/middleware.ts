// Loads the session cookie on every request and exposes the user
// (or null) at Astro.locals.user. Pages and components read it
// directly; no per-page boilerplate.

import { defineMiddleware } from "astro:middleware";
import { readSessionCookie } from "@/lib/auth";

export const onRequest = defineMiddleware(async (ctx, next) => {
  ctx.locals.user = await readSessionCookie(ctx);
  return next();
});
