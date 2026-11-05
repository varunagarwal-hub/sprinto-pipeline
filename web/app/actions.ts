"use server";

import { revalidatePath } from "next/cache";
import { distance } from "fastest-levenshtein";
import { sql } from "@/lib/db";
import { capture, humanDecision } from "@/lib/feedback";

export type ActionResult = { ok: boolean; message: string };

const refresh = () => {
  for (const p of ["/", "/review", "/drafts", "/companies", "/loop"]) revalidatePath(p);
};
const done = (message: string): ActionResult => (refresh(), { ok: true, message });
const fail = (message: string): ActionResult => ({ ok: false, message });

function normaliseDomain(raw: string): string | null {
  const s = raw.trim().toLowerCase();
  try {
    const host = new URL(s.includes("//") ? s : `https://${s}`).hostname.replace(/^www\./, "");
    return host.includes(".") ? host : null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------- review queue

export async function qualifyCompany(companyId: number, note: string): Promise<ActionResult> {
  const [c] = await sql`SELECT id, status, domain, EXISTS (SELECT 1 FROM contacts k WHERE k.company_id = c.id) has_contact,
                               EXISTS (SELECT 1 FROM emails e WHERE e.company_id = c.id) has_email
                        FROM companies c WHERE id = ${companyId}`;
  if (!c) return fail("Company not found");
  if (!c.domain) return fail("Needs a domain first: use Provide domain.");
  const queued = !c.has_contact ? "poc_write" : !c.has_email ? "regenerate" : null;
  await humanDecision(companyId, "status", { from: c.status, to: "qualified" }, note || "qualified by reviewer");
  await sql`UPDATE companies SET status = 'qualified', needs_review = false, flag_reason = NULL, drop_reason = NULL,
            queued_action = ${queued}, updated_at = now() WHERE id = ${companyId}`;
  await capture({ kind: "override_qualify", source: "reviewer", companyId, note, payload: { from: c.status, queued } });
  return done(queued ? `Qualified. ${queued === "poc_write" ? "PoC + draft" : "Draft"} queued for the next pipeline sync.` : "Qualified.");
}

export async function approveReview(companyId: number, note: string): Promise<ActionResult> {
  await humanDecision(companyId, "review", { needs_review: false }, note || "reviewed and approved as is");
  await sql`UPDATE companies SET needs_review = false, updated_at = now() WHERE id = ${companyId}`;
  await capture({ kind: "review_approved", source: "reviewer", companyId, note });
  return done("Review cleared.");
}

export async function dropCompany(companyId: number, reason: string): Promise<ActionResult> {
  const why = reason.trim() || "dropped by reviewer";
  const [c] = await sql`SELECT status FROM companies WHERE id = ${companyId}`;
  await humanDecision(companyId, "status", { from: c?.status, to: "dropped" }, why);
  await sql`UPDATE companies SET status = 'dropped', needs_review = false, drop_reason = ${"human: " + why},
            queued_action = NULL, updated_at = now() WHERE id = ${companyId}`;
  await sql`UPDATE emails SET status = 'discarded' WHERE company_id = ${companyId} AND status IN ('drafted','flagged')`;
  await capture({ kind: "override_drop", source: "reviewer", companyId, note: why, payload: { from: c?.status } });
  return done("Dropped.");
}

export async function provideDomain(companyId: number, raw: string): Promise<ActionResult> {
  const domain = normaliseDomain(raw);
  if (!domain) return fail(`'${raw}' is not a domain.`);
  await humanDecision(companyId, "domain", { domain }, "domain provided by reviewer");
  await sql`UPDATE companies SET domain = ${domain}, domain_source = 'human', queued_action = 'research',
            updated_at = now() WHERE id = ${companyId}`;
  await capture({ kind: "domain_provided", source: "reviewer", companyId, payload: { domain } });
  return done(`Saved ${domain}. Research is queued: run "python main.py sync" to crawl, qualify and draft it.`);
}

// ---------------------------------------------------------------- drafts

export async function switchHook(emailId: number, evidenceId: string): Promise<ActionResult> {
  const [e] = await sql`SELECT company_id::int, hook, evidence_catalog FROM emails WHERE id = ${emailId}`;
  if (!e) return fail("Draft not found");
  const cat = (e.evidence_catalog ?? []) as { id: string; field: string }[];
  const from = e.hook?.evidence_id;
  const value = { evidence_id: evidenceId, from, from_field: cat.find((i) => i.id === from)?.field,
                  to_field: cat.find((i) => i.id === evidenceId)?.field };
  await humanDecision(e.company_id, "hook", value, "hook chosen by reviewer");
  await sql`UPDATE companies SET chosen_hook = ${JSON.stringify({ evidence_id: evidenceId, by: "human" })}::jsonb,
            needs_review = false, queued_action = 'regenerate', updated_at = now() WHERE id = ${e.company_id}`;
  await capture({ kind: "hook_switch", source: "reviewer", companyId: e.company_id, emailId, payload: value });
  return done(`Hook set to ${evidenceId}. Regeneration queued for the next pipeline sync.`);
}

export async function rateDraft(emailId: number, rating: 1 | -1, tags: string[], note: string): Promise<ActionResult> {
  const [e] = await sql`UPDATE emails SET rating = ${rating} WHERE id = ${emailId} RETURNING company_id::int`;
  if (!e) return fail("Draft not found");
  await capture({ kind: "draft_rating", source: "reviewer", companyId: e.company_id, emailId, rating, tags,
                  note: note || null });
  return done(rating > 0 ? "Thanks: marked as a good draft." : "Captured: the writer will learn from this.");
}

export async function sendDraft(emailId: number, subject: string, body: string): Promise<ActionResult> {
  const { RESEND_API_KEY, MY_EMAIL } = process.env;
  const from = process.env.RESEND_FROM || "Sprinto Outreach <onboarding@resend.dev>";
  if (!RESEND_API_KEY || !MY_EMAIL) return fail("RESEND_API_KEY and MY_EMAIL must be set on the server.");
  const [e] = await sql`
    SELECT e.id, e.status, e.original_body, e.subject, e.learnings_applied, e.company_id::int, co.name company,
           k.full_name poc FROM emails e JOIN companies co ON co.id = e.company_id
    LEFT JOIN contacts k ON k.id = e.contact_id WHERE e.id = ${emailId}`;
  if (!e) return fail("Draft not found");
  if (e.status === "sent") return fail("Already sent.");
  if (!body.trim()) return fail("Body is empty.");
  const fullSubject = `[DRAFT for ${e.poc ?? "?"} @ ${e.company}] ${subject || e.subject}`;
  // Recipient is always MY_EMAIL. There is deliberately no way to address the prospect from here.
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${RESEND_API_KEY}`, "Content-Type": "application/json",
               "Idempotency-Key": `sprinto-web-${emailId}-${body.length}-${fullSubject.length}` },
    body: JSON.stringify({ from, to: [MY_EMAIL], subject: fullSubject, text: body }),
  });
  if (!res.ok) return fail(`Resend: HTTP ${res.status} ${(await res.text()).slice(0, 200)}`);
  const { id: messageId } = (await res.json()) as { id?: string };
  const original = e.original_body ?? "";
  const rate = Math.round((distance(original, body) / Math.max(original.length, body.length, 1)) * 10000) / 10000;
  await sql`UPDATE emails SET sent_body = ${body}, sent_subject = ${fullSubject}, sent_to = ${MY_EMAIL},
            edit_distance = ${rate}, provider_message_id = ${messageId ?? null}, status = 'sent', sent_at = now()
            WHERE id = ${emailId}`;
  await sql`UPDATE companies SET status = 'sent', updated_at = now() WHERE id = ${e.company_id}`;
  await sql`INSERT INTO api_usage (company_id, service, stage, ok) VALUES (${e.company_id}, 'resend', 'send', true)`;
  await capture({ kind: "draft_sent", source: "reviewer", companyId: e.company_id, emailId,
                  payload: { edit_rate: rate, original_body: original, sent_body: body, original_subject: e.subject,
                             sent_subject: subject, learnings_applied: e.learnings_applied ?? [] } });
  return done(`Sent to ${MY_EMAIL} · edit rate ${(rate * 100).toFixed(1)}%`);
}

export async function setOutcome(emailId: number, outcome: string, note: string): Promise<ActionResult> {
  const allowed = ["replied", "meeting", "not_interested", "no_reply", "bounced"];
  if (!allowed.includes(outcome)) return fail("Unknown outcome");
  const [e] = await sql`UPDATE emails SET outcome = ${outcome}, outcome_at = now() WHERE id = ${emailId}
                        RETURNING company_id::int`;
  if (!e) return fail("Email not found");
  await capture({ kind: "outcome", source: "prospect", companyId: e.company_id, emailId, note: note || null,
                  rating: ["replied", "meeting"].includes(outcome) ? 1 : -1, payload: { outcome } });
  return done(`Outcome recorded: ${outcome.replace("_", " ")}.`);
}

// ---------------------------------------------------------------- learnings

export async function setLearningStatus(id: number, active: boolean): Promise<ActionResult> {
  await sql`UPDATE learnings SET status = ${active ? "active" : "retired"}, updated_at = now() WHERE id = ${id}`;
  return done(active ? "Learning re-activated." : "Learning retired: it will no longer be retrieved.");
}
