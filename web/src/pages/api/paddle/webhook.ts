import type { APIRoute } from "astro";
import { recordEvent, applyEvent, verifySignature } from "@/lib/paddle";

export const prerender = false;

export const POST: APIRoute = async ({ request }) => {
  const body = await request.text();
  const sig = request.headers.get("paddle-signature") ?? "";
  const secret = process.env.PADDLE_WEBHOOK_SECRET ?? "";
  if (secret && !(await verifySignature(body, sig, secret))) {
    return new Response("bad signature", { status: 401 });
  }
  let evt: { event_id: string; event_type: string; data: Record<string, unknown> };
  try {
    evt = JSON.parse(body);
  } catch {
    return new Response("bad json", { status: 400 });
  }
  await recordEvent(evt);
  // Apply async-ish — but we await here so failures bubble up to
  // Paddle's retry. Volume is low (<10/min); no need to background.
  try {
    await applyEvent(evt);
  } catch (err) {
    console.error("paddle apply failed", evt.event_id, err);
    return new Response("apply failed", { status: 500 });
  }
  return new Response("ok", { status: 200 });
};
