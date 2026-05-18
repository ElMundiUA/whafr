import type { APIRoute } from "astro";
import { query } from "@/lib/db";

export const prerender = false;

// Manual tier comp — admin sets a user to free or pro without
// going through Paddle. Bypasses subscription state entirely.
// Useful for internal team accounts, beta-testers, and rescuing
// users whose Paddle webhook arrived garbled.
//
// We don't unset paddle_subscription_id when downgrading manually,
// because Paddle remains the source of truth if the user later
// pays — and a future webhook will overwrite tier anyway. Manual
// upgrades set pro_until = NULL so Pro never expires from the
// admin's POV (Paddle would normally set it to the renewal date).

export const POST: APIRoute = async ({ request, locals }) => {
  if (!locals.user?.is_admin) {
    return new Response("forbidden", { status: 403 });
  }
  const form = await request.formData();
  const userId = String(form.get("user_id") ?? "");
  const tier = String(form.get("tier") ?? "");
  if (!userId || !["free", "pro"].includes(tier)) {
    return new Response("bad request", { status: 400 });
  }

  await query(
    `UPDATE users
        SET tier = $1,
            pro_until = CASE WHEN $1 = 'pro' THEN NULL ELSE pro_until END
      WHERE id = $2`,
    [tier, Number(userId)],
  );

  // Redirect back to wherever the form was submitted from. Keeps
  // the admin on the user list with their scroll position roughly
  // preserved (browsers re-render the table at the same offset).
  const back = request.headers.get("referer") ?? "/admin/users";
  return new Response(null, { status: 303, headers: { Location: back } });
};
