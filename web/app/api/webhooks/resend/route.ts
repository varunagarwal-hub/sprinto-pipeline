import { createHmac, timingSafeEqual } from "node:crypto";
import { sql } from "@/lib/db";
import { capture } from "@/lib/feedback";

/**
 * Resend → Signal → Capture. Configure in Resend: Webhooks → endpoint https://<app>/api/webhooks/resend,
 * events email.delivered / bounced / opened / clicked / complained; put the signing secret in
 * RESEND_WEBHOOK_SECRET. Requests without a valid Svix signature are rejected.
 */
function verify(secret: string, id: string, ts: string, body: string, header: string): boolean {
  const key = Buffer.from(secret.replace(/^whsec_/, ""), "base64");
  const expected = createHmac("sha256", key).update(`${id}.${ts}.${body}`).digest();
  if (Math.abs(Date.now() / 1000 - Number(ts)) > 300) return false;
  return header.split(" ").some((part) => {
    const sig = Buffer.from(part.split(",")[1] ?? "", "base64");
    return sig.length === expected.length && timingSafeEqual(sig, expected);
  });
}

export async function POST(req: Request) {
  const secret = process.env.RESEND_WEBHOOK_SECRET;
  const body = await req.text();
  const [id, ts, sig] = ["svix-id", "svix-timestamp", "svix-signature"].map((h) => req.headers.get(h) ?? "");
  if (!secret || !id || !verify(secret, id, ts, body, sig)) {
    return Response.json({ error: "invalid signature" }, { status: 401 });
  }
  const event = JSON.parse(body) as { type: string; data?: { email_id?: string } };
  const messageId = event.data?.email_id;
  const [e] = messageId
    ? await sql`SELECT id::int, company_id::int FROM emails WHERE provider_message_id = ${messageId}`
    : [];
  if (!e) return Response.json({ ok: true, matched: false });

  const type = event.type.replace(/^email\./, "");
  await capture({ kind: "delivery", source: "webhook", companyId: e.company_id, emailId: e.id,
                  payload: { type, resend_event_id: id } });
  if (type === "bounced") {
    await sql`UPDATE emails SET outcome = 'bounced', outcome_at = now() WHERE id = ${e.id} AND outcome IS NULL`;
    await capture({ kind: "outcome", source: "webhook", companyId: e.company_id, emailId: e.id, rating: -1,
                    payload: { outcome: "bounced" } });
  }
  return Response.json({ ok: true, matched: true, type });
}
