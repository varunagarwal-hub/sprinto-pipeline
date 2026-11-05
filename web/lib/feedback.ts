import "server-only";
import { sql } from "@/lib/db";

/** Mirrors pipeline/feedback.py so events captured on the web are identical to ones captured by the CLI. */
const hits = (text: string, words: string[]) =>
  words.some((w) => new RegExp(`(?<![a-z0-9])${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![a-z0-9])`).test(text));

export function roleKey(title?: string | null): "security" | "executive" | "engineering" {
  const t = (title ?? "").toLowerCase();
  if (hits(t, ["ciso", "security", "compliance", "grc", "risk"])) return "security";
  if (hits(t, ["ceo", "chief executive", "founder", "co-founder", "president", "coo", "chief operating"])) return "executive";
  return "engineering";
}

export function hookGroup(field?: string | null): string {
  const f = field ?? "";
  if (f.startsWith("audit_signal")) return "audit_signal";
  return f === "funding" || f === "certification" ? f : "other";
}

async function context(companyId: number | null, emailId: number | null) {
  if (!companyId) return {};
  const [c] = await sql`
    SELECT c.funding_stage, c.industry_tags, c.fit_score::float8 AS fit,
           (SELECT title FROM contacts k WHERE k.company_id = c.id ORDER BY id DESC LIMIT 1) AS poc_title
    FROM companies c WHERE c.id = ${companyId}`;
  if (!c) return {};
  let group = "other";
  if (emailId) {
    const [e] = await sql`SELECT hook, evidence_catalog FROM emails WHERE id = ${emailId}`;
    const hookId = e?.hook?.evidence_id;
    const item = (e?.evidence_catalog ?? []).find((it: { id: string }) => it.id === hookId);
    group = hookGroup(item?.field);
  }
  return { stage: c.funding_stage, industry: c.industry_tags ?? [], fit: c.fit, role: roleKey(c.poc_title), hook_group: group };
}

export type FeedbackKind =
  | "draft_sent" | "draft_rating" | "hook_switch" | "override_qualify" | "override_drop"
  | "review_approved" | "domain_provided" | "outcome" | "delivery";

/** CAPTURE. Never throws: losing a feedback event must not fail the reviewer's action. */
export async function capture(opts: {
  kind: FeedbackKind; source: "reviewer" | "prospect" | "webhook" | "system";
  companyId?: number | null; emailId?: number | null; rating?: number | null;
  tags?: string[]; note?: string | null; payload?: Record<string, unknown>;
}) {
  try {
    const ctx = await context(opts.companyId ?? null, opts.emailId ?? null);
    await sql`
      INSERT INTO feedback_events (company_id, email_id, source, kind, rating, tags, note, payload, context)
      VALUES (${opts.companyId ?? null}, ${opts.emailId ?? null}, ${opts.source}, ${opts.kind}, ${opts.rating ?? null},
              ${opts.tags ?? []}, ${opts.note ?? null}, ${JSON.stringify(opts.payload ?? {})}::jsonb, ${JSON.stringify(ctx)}::jsonb)`;
  } catch (err) {
    console.error("feedback capture failed", opts.kind, err);
  }
}

export async function humanDecision(companyId: number, gate: string, value: unknown, reason: string) {
  await sql`
    INSERT INTO decisions (company_id, run_id, stage, gate, result, value, reason, decided_by)
    VALUES (${companyId}, (SELECT last_run_id FROM companies WHERE id = ${companyId}), 'human', ${gate}, 'override',
            ${JSON.stringify(value)}::jsonb, ${reason}, 'human')`;
}
