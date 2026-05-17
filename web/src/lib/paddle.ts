// Paddle Billing webhook handler. We accept the events that move a
// user between free / pro and ignore the rest. Each event is logged
// first; the apply path is best-effort — re-run-safe via event_id.

import { query } from "@/lib/db";

interface PaddleEvent {
  event_id: string;
  event_type: string;
  data: {
    id?: string;
    customer_id?: string;
    custom_data?: { auth0_sub?: string; email?: string };
    status?: string;
    current_billing_period?: { ends_at?: string };
    items?: { price?: { id?: string } }[];
  };
}

const PRO_PRICE_ID = import.meta.env.PADDLE_PRODUCT_PRO_MONTHLY ?? "";

export async function recordEvent(evt: PaddleEvent): Promise<void> {
  await query(
    `INSERT INTO paddle_events (event_id, event_type, payload)
     VALUES ($1, $2, $3)
     ON CONFLICT (event_id) DO NOTHING`,
    [evt.event_id, evt.event_type, JSON.stringify(evt)],
  );
}

export async function applyEvent(evt: PaddleEvent): Promise<void> {
  const data = evt.data;
  const customData = data.custom_data ?? {};
  const auth0Sub = customData.auth0_sub;
  const email = customData.email;

  // We need a way to identify the user. custom_data.auth0_sub is
  // attached at checkout-link creation; if it's missing, fall back
  // to email and hope.
  if (!auth0Sub && !email) {
    await markProcessed(evt.event_id);
    return;
  }

  const subscriptionId = data.id;
  const customerId = data.customer_id ?? null;
  const status = data.status;
  const periodEnd = data.current_billing_period?.ends_at ?? null;
  const isPro =
    evt.event_type === "subscription.created" ||
    evt.event_type === "subscription.activated" ||
    (evt.event_type === "subscription.updated" && status === "active");

  const isFree =
    evt.event_type === "subscription.canceled" ||
    evt.event_type === "subscription.paused" ||
    (evt.event_type === "subscription.updated" && status === "canceled");

  if (!isPro && !isFree) {
    await markProcessed(evt.event_id);
    return;
  }

  // Verify the item matches our Pro price (defence-in-depth — a
  // future second product shouldn't accidentally upgrade everyone).
  if (isPro && PRO_PRICE_ID) {
    const priceIds = (data.items ?? []).map((i) => i.price?.id).filter(Boolean);
    if (priceIds.length > 0 && !priceIds.includes(PRO_PRICE_ID)) {
      await markProcessed(evt.event_id);
      return;
    }
  }

  const tier = isPro ? "pro" : "free";
  const sql = auth0Sub
    ? `UPDATE users SET tier = $1, paddle_customer_id = $2,
              paddle_subscription_id = $3, pro_until = $4
       WHERE auth0_sub = $5`
    : `UPDATE users SET tier = $1, paddle_customer_id = $2,
              paddle_subscription_id = $3, pro_until = $4
       WHERE email = $5`;
  await query(sql, [
    tier,
    customerId,
    subscriptionId ?? null,
    periodEnd,
    auth0Sub ?? email,
  ]);
  await markProcessed(evt.event_id);
}

async function markProcessed(eventId: string): Promise<void> {
  await query(
    `UPDATE paddle_events SET processed_at = NOW() WHERE event_id = $1`,
    [eventId],
  );
}

// Paddle signs webhook bodies with an HMAC-SHA256 over a
// timestamp-prefixed payload. The header has the form
// `ts=…;h1=…`. Verifying signatures here is more important than
// it looks — without it, anyone with a Paddle webhook URL guess
// can upgrade themselves to Pro.
export async function verifySignature(
  body: string,
  signature: string,
  secret: string,
): Promise<boolean> {
  const parts = Object.fromEntries(
    signature.split(";").map((s) => s.split("=") as [string, string]),
  );
  const ts = parts.ts;
  const h1 = parts.h1;
  if (!ts || !h1) return false;

  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(`${ts}:${body}`));
  const hex = Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");

  // Constant-time compare.
  if (hex.length !== h1.length) return false;
  let mismatch = 0;
  for (let i = 0; i < hex.length; i++) {
    mismatch |= hex.charCodeAt(i) ^ h1.charCodeAt(i);
  }
  return mismatch === 0;
}

export function checkoutUrl(auth0Sub: string, email: string): string {
  const base = import.meta.env.PADDLE_ENV === "production"
    ? "https://buy.paddle.com/product"
    : "https://sandbox-buy.paddle.com/product";
  const productId = import.meta.env.PADDLE_PRODUCT_PRO_MONTHLY ?? "";
  const u = new URL(`${base}/${productId}`);
  // Paddle Classic format — for Billing v2 the checkout link is
  // created server-side via API. Stub for now; swap to Paddle.js
  // overlay once the product is set up.
  u.searchParams.set("passthrough", JSON.stringify({ auth0_sub: auth0Sub, email }));
  return u.toString();
}
