import "server-only";
import { sql, type Row } from "@/lib/db";

const COOLDOWN_DAYS = 90;
export const BATCH_SIZE = 20;
export const TARGET_QUALIFIED = 5;
export const MAX_BATCHES = 3;
export const MAX_FETCHES = 6;

export async function getRuns() {
  return sql`
    SELECT id::int, started_at, finished_at, status, stop_reason, pool_size, batches_used, researched, qualified,
           flagged, dropped FROM runs ORDER BY id DESC`;
}

export async function getOverview(runId: number | null) {
  const [eligible, funnel, hunter, tokens, drops, flags, comps, b1, fetches, editRate, activity] = await Promise.all([
    sql`SELECT count(*)::int n FROM companies WHERE status = 'pooled'
          AND (seen_at IS NULL OR seen_at < now() - make_interval(days => ${COOLDOWN_DAYS}))`,
    sql`SELECT (SELECT count(*)::int FROM companies) pool,
               (SELECT count(*)::int FROM companies WHERE last_run_id IS NOT NULL) researched,
               (SELECT count(*)::int FROM companies WHERE status IN ('qualified','drafted','sent')) qualified,
               (SELECT count(DISTINCT company_id)::int FROM emails WHERE status IN ('drafted','sent')) drafted,
               (SELECT count(*)::int FROM emails WHERE status = 'sent') sent`,
    sql`SELECT coalesce(sum(credits_used) FILTER (WHERE NOT cached AND ok), 0)::int spent,
               (SELECT credits_remaining FROM api_usage WHERE service = 'hunter' AND model = 'live'
                AND credits_remaining IS NOT NULL ORDER BY ts DESC LIMIT 1)::int remaining
        FROM api_usage WHERE service = 'hunter'`,
    sql`SELECT stage, count(*)::int calls, count(*) FILTER (WHERE cached)::int cached,
               coalesce(sum(tokens_in) FILTER (WHERE NOT cached), 0)::int tokens_in,
               coalesce(sum(tokens_out) FILTER (WHERE NOT cached), 0)::int tokens_out
        FROM api_usage WHERE service = 'gemini' AND (${runId}::bigint IS NULL OR run_id = ${runId})
        GROUP BY stage ORDER BY stage`,
    sql`SELECT split_part(split_part(drop_reason, ';', 1), ':', 1) AS reason, count(*)::int n,
               string_agg(name, ', ') companies
        FROM companies WHERE status = 'dropped' AND (${runId}::bigint IS NULL OR last_run_id = ${runId})
        GROUP BY 1 ORDER BY 2 DESC`,
    sql`SELECT trim(split_part(split_part(flag_reason, '(', 1), ':', 1)) AS reason, count(*)::int n,
               string_agg(name, ', ') companies
        FROM companies WHERE (status = 'flagged' OR needs_review) AND flag_reason IS NOT NULL
          AND (${runId}::bigint IS NULL OR last_run_id = ${runId})
        GROUP BY 1 ORDER BY 2 DESC`,
    sql`SELECT id::int, name, domain, funding_stage, rank_score::float8 rank, status, needs_review,
               fit_score::float8 fit, (evidence->>'_fetches')::int fetches, coalesce(drop_reason, flag_reason) reason,
               queued_action
        FROM companies WHERE (${runId}::bigint IS NULL AND last_run_id IS NOT NULL) OR last_run_id = ${runId}
        ORDER BY updated_at DESC`,
    sql`SELECT count(*)::int n,
               count(*) FILTER (WHERE status IN ('qualified','drafted','sent') OR flag_reason ILIKE '%PoC%'
                                OR flag_reason ILIKE 'email failed%')::int q
        FROM companies WHERE research_batch = 1 AND (${runId}::bigint IS NULL OR last_run_id = ${runId})`,
    sql`SELECT avg((evidence->>'_fetches')::int)::float8 avg, max((evidence->>'_fetches')::int)::int max
        FROM companies WHERE evidence ? '_fetches' AND (${runId}::bigint IS NULL OR last_run_id = ${runId})`,
    sql`SELECT avg(edit_distance)::float8 avg, count(*)::int n FROM emails WHERE status = 'sent'`,
    sql`SELECT f.id::int, f.created_at, f.kind, f.source, f.rating, f.note, c.name company, c.id::int company_id
        FROM feedback_events f LEFT JOIN companies c ON c.id = f.company_id ORDER BY f.created_at DESC LIMIT 8`,
  ]);
  return {
    eligible: eligible[0].n as number, funnel: funnel[0], hunter: hunter[0], tokens, drops, flags, companies: comps,
    batch1: b1[0], fetches: fetches[0], editRate: editRate[0], activity,
  };
}

export async function getReviewQueue() {
  return sql`
    SELECT c.id::int, c.name, c.domain, c.funding_stage, c.status, c.needs_review, c.flag_reason, c.unknown_fields,
           c.queued_action, c.rank_score::float8 rank, c.fit_score::float8 fit, c.article_url,
           (SELECT g FROM jsonb_array_elements(c.stage1_evidence) g WHERE g->>'gate' = 'funding_stage' LIMIT 1) funding,
           (SELECT json_agg(d ORDER BY d.id) FROM (
              SELECT id, stage, gate, result, evidence_quote, source_url, evidence_date, reason, decided_by
              FROM decisions WHERE company_id = c.id AND stage IN ('resolve','qualify','poc','human')) d) gates,
           EXISTS (SELECT 1 FROM contacts k WHERE k.company_id = c.id) has_contact
    FROM companies c
    WHERE c.status = 'flagged' OR (c.needs_review AND c.status IN ('qualified','drafted'))
    ORDER BY (c.status = 'flagged') DESC, c.rank_score DESC`;
}

export async function getDrafts() {
  return sql`
    SELECT e.id::int, e.company_id::int, e.status, e.subject, e.original_body, e.hook, e.pain_points, e.claims,
           e.evidence_catalog, e.hook_candidates, e.validator_log, e.attempts, e.rating, e.learnings_applied,
           e.created_at, co.name company, co.domain, co.funding_stage, co.needs_review, co.queued_action,
           co.fit_score::float8 fit, k.full_name, k.first_name, k.title, k.email AS poc_email, k.is_mock, k.confidence,
           k.verification_status, k.pick_reason,
           (SELECT coalesce(json_agg(json_build_object('id', l.id, 'scope', l.scope, 'lesson', l.lesson)), '[]'::json)
              FROM learnings l WHERE l.id = ANY(e.learnings_applied)) lessons
    FROM emails e JOIN companies co ON co.id = e.company_id LEFT JOIN contacts k ON k.id = e.contact_id
    WHERE e.status IN ('drafted', 'flagged')
    ORDER BY co.fit_score DESC NULLS LAST, e.id DESC`;
}

export async function getSent() {
  return sql`
    SELECT e.id::int, e.company_id::int, co.name company, k.full_name, k.title, e.sent_subject, e.sent_to, e.sent_at,
           e.edit_distance::float8 edit_rate, e.outcome, e.outcome_at, e.rating, e.provider_message_id,
           cardinality(e.learnings_applied)::int learnings
    FROM emails e JOIN companies co ON co.id = e.company_id LEFT JOIN contacts k ON k.id = e.contact_id
    WHERE e.status = 'sent' ORDER BY e.sent_at DESC`;
}

export async function getCompanies(q: string | null, status: string | null) {
  const like = q ? `%${q}%` : null;
  return sql`
    SELECT id::int, name, domain, funding_stage, funding_date, rank_score::float8 rank, status, needs_review,
           fit_score::float8 fit, industry_tags, coalesce(drop_reason, flag_reason) reason, updated_at
    FROM companies
    WHERE (last_run_id IS NOT NULL OR source = 'manual')
      AND (${like}::text IS NULL OR name ILIKE ${like} OR domain ILIKE ${like})
      AND (${status}::text IS NULL OR status = ${status})
    ORDER BY updated_at DESC LIMIT 200`;
}

export async function getCompany(id: number) {
  const [[c], decisions, pages, contacts, emails, events, applied] = await Promise.all([
    sql`SELECT *, id::int, rank_score::float8 rank, fit_score::float8 fit FROM companies WHERE id = ${id}`,
    sql`SELECT id::int, stage, gate, result, value, evidence_quote, source_url, evidence_date, reason, decided_by, created_at
        FROM decisions WHERE company_id = ${id} ORDER BY id`,
    sql`SELECT id::int, iteration, target_field, page_type, http_status, text_chars, page_url, fetched_at
        FROM research WHERE company_id = ${id} ORDER BY id`,
    sql`SELECT id::int, full_name, title, email, confidence, verification_status, is_mock, pick_rule, pick_reason,
               created_at, hunter_raw->'meta' AS meta
        FROM contacts WHERE company_id = ${id} ORDER BY id DESC`,
    sql`SELECT id::int, status, subject, original_body, sent_body, sent_subject, sent_to, sent_at, edit_distance::float8 edit_rate,
               hook, outcome, rating, learnings_applied, created_at
        FROM emails WHERE company_id = ${id} ORDER BY id DESC`,
    sql`SELECT id::int, kind, source, rating, tags, note, payload, created_at FROM feedback_events
        WHERE company_id = ${id} ORDER BY created_at DESC`,
    sql`SELECT DISTINCT l.id::int, l.scope, l.lesson FROM learning_applications a JOIN learnings l ON l.id = a.learning_id
        WHERE a.company_id = ${id}`,
  ]);
  return c ? { company: c as Row, decisions, pages, contacts, emails, events, applied } : null;
}

export async function getLoop() {
  const [stages, learnings, events, byKind, effect, perScope] = await Promise.all([
    sql`SELECT
          (SELECT count(*)::int FROM emails WHERE status IN ('drafted','flagged','sent')) drafts,
          (SELECT count(*)::int FROM emails WHERE status = 'sent') sends,
          (SELECT count(*)::int FROM emails WHERE status = 'sent' OR rating IS NOT NULL OR outcome IS NOT NULL) signals,
          (SELECT count(*)::int FROM decisions WHERE decided_by = 'human') human_decisions,
          (SELECT count(*)::int FROM feedback_events) captured,
          (SELECT count(*)::int FROM feedback_events WHERE processed_at IS NULL) unprocessed,
          (SELECT count(*)::int FROM learnings WHERE status = 'active') stored,
          (SELECT count(*)::int FROM learning_applications) retrievals,
          (SELECT count(*)::int FROM emails WHERE cardinality(learnings_applied) > 0) applied_drafts,
          (SELECT max(processed_at) FROM feedback_events) last_learned`,
    sql`SELECT id::int, scope, kind, key, lesson, applies_to, value::float8, support, confidence::float8, status,
               times_applied, last_applied_at, cardinality(evidence_event_ids)::int evidence, updated_at
        FROM learnings ORDER BY status, support * confidence DESC, updated_at DESC`,
    sql`SELECT f.id::int, f.created_at, f.kind, f.source, f.rating, f.tags, f.note, f.processed_at,
               f.payload->>'edit_rate' edit_rate, f.payload->>'outcome' outcome, c.name company, c.id::int company_id
        FROM feedback_events f LEFT JOIN companies c ON c.id = f.company_id ORDER BY f.created_at DESC LIMIT 40`,
    sql`SELECT kind, count(*)::int n FROM feedback_events GROUP BY kind ORDER BY 2 DESC`,
    sql`SELECT (cardinality(learnings_applied) > 0) with_learnings, count(*)::int n, avg(edit_distance)::float8 edit_rate,
               count(*) FILTER (WHERE outcome IN ('replied','meeting'))::int positive
        FROM emails WHERE status = 'sent' GROUP BY 1`,
    sql`SELECT scope, count(*)::int n FROM learnings WHERE status = 'active' GROUP BY scope`,
  ]);
  return { stages: stages[0], learnings, events, byKind, effect, perScope };
}
