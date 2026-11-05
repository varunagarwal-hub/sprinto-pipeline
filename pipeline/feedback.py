"""The feedback loop: Act -> Signal -> Capture -> Store -> Retrieve -> Apply.

Act     drafts and sends (write.py, mailer.py)
Signal  reviewer edits, ratings, hook switches, overrides; prospect outcomes; Resend delivery webhooks
Capture capture(): one feedback_events row per signal, with the context needed to generalise from it
Store   learn(): distil events into `learnings` (deterministic stats + one Gemini summarisation of edits)
Retrieve retrieve(): learnings matching a company's role / industry / stage
Apply   writer prompt + validator (write.py), hook ordering (write.py), rank adjustment (stage1.py);
        every use is recorded in learning_applications and emails.learnings_applied

Gemini only summarises edits into candidate lessons; Python decides which lessons are kept and how
strongly they apply (support counts, thresholds, clamps).
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from typing import Any, Literal, Optional

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from pipeline import db
from pipeline.stage1 import keyword_hits

log = logging.getLogger(__name__)

MIN_PHRASE_SUPPORT = 2        # a removed/added phrase must recur in this many sends to become a rule
MIN_HOOK_SUPPORT = 3          # outcomes per hook type before hook ordering changes
MIN_INDUSTRY_SUPPORT = 2      # reviewer decisions per industry before rank changes
MAX_INDUSTRY_ADJUST = 0.15
POSITIVE_OUTCOMES = {"replied", "meeting"}
NEGATIVE_OUTCOMES = {"no_reply", "not_interested"}
STOP = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "your", "you", "we", "our", "is",
        "are", "it", "this", "that", "as", "at", "by", "be", "so", "can", "from", "hi", "i"}


# ---------------------------------------------------------------- shared helpers (pure)

def role_key(title: str | None) -> str:
    t = (title or "").lower()
    if keyword_hits(t, ["ciso", "security", "compliance", "grc", "risk"]):
        return "security"
    if keyword_hits(t, ["ceo", "chief executive", "founder", "co-founder", "president", "coo", "chief operating"]):
        return "executive"
    return "engineering"


def hook_group(field: str | None) -> str:
    f = field or ""
    if f.startswith("audit_signal"):
        return "audit_signal"
    return f if f in ("funding", "certification") else "other"


def ngrams(text: str, n: int = 3) -> set[str]:
    words = re.findall(r"[a-z][a-z'-]*", (text or "").lower())
    grams = set()
    for i in range(len(words) - n + 1):
        g = words[i:i + n]
        if sum(w not in STOP for w in g) >= 2:        # at least two content words
            grams.add(" ".join(g))
    return grams


def phrase_diffs(pairs: list[tuple[int, str, str]]) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """[(event_id, original, sent)] -> phrases removed / added, each with the events that did it."""
    removed: dict[str, list[int]] = defaultdict(list)
    added: dict[str, list[int]] = defaultdict(list)
    for eid, original, sent in pairs:
        o, s = ngrams(original), ngrams(sent)
        for g in o - s:
            removed[g].append(eid)
        for g in s - o:
            added[g].append(eid)
    return removed, added


def hook_scores_from(outcomes: list[tuple[str, str]], switches: list[tuple[str, str]]) -> dict[str, dict]:
    """outcomes [(hook_group, outcome)], switches [(from_group, to_group)] -> Laplace-smoothed score per group."""
    pos: Counter = Counter()
    n: Counter = Counter()
    for g, o in outcomes:
        if o in POSITIVE_OUTCOMES:
            pos[g] += 1
            n[g] += 1
        elif o in NEGATIVE_OUTCOMES:
            n[g] += 1
    for frm, to in switches:          # a reviewer switching hooks is a weak preference signal
        pos[to] += 0.5
        n[to] += 0.5
        n[frm] += 0.5
    return {g: {"score": round((pos[g] + 1) / (n[g] + 2), 3), "support": n[g]} for g in n}


def industry_adjust_from(decisions: list[tuple[list[str], int]]) -> dict[str, dict]:
    """[(industry_tags, +1 qualify/positive outcome | -1 drop)] -> bounded rank delta per industry."""
    tally: dict[str, list[int]] = defaultdict(list)
    for tags, sign in decisions:
        for t in tags or ["(untagged)"]:
            tally[t].append(sign)
    out = {}
    for ind, signs in tally.items():
        delta = max(-MAX_INDUSTRY_ADJUST, min(MAX_INDUSTRY_ADJUST, 0.1 * sum(signs) / (len(signs) + 1)))
        out[ind] = {"delta": round(delta, 3), "support": len(signs), "pos": signs.count(1), "neg": signs.count(-1)}
    return out


def context_matches(applies_to: dict, ctx: dict) -> bool:
    """A learning applies if every key it is scoped to matches the company context ({} = global)."""
    for k, v in (applies_to or {}).items():
        if v in (None, "", "any"):
            continue
        have = ctx.get(k)
        if isinstance(have, (list, tuple, set)):
            if v not in have:
                return False
        elif have != v:
            return False
    return True


# ---------------------------------------------------------------- CAPTURE

def _context(company_id: int | None, email_id: int | None) -> dict:
    if not company_id:
        return {}
    with db.connect() as conn:
        row = conn.execute(
            """SELECT c.funding_stage, c.industry_tags, c.fit_score, c.status,
                      (SELECT title FROM contacts k WHERE k.company_id=c.id ORDER BY id DESC LIMIT 1) AS poc_title,
                      (SELECT hook FROM emails e WHERE e.id=%s) AS hook
               FROM companies c WHERE c.id=%s""", (email_id, company_id)).fetchone()
    if not row:
        return {}
    catalog_field = None
    if row["hook"] and email_id:
        with db.connect() as conn:
            cat = conn.execute("SELECT evidence_catalog FROM emails WHERE id=%s", (email_id,)).fetchone()
        catalog_field = next((it["field"] for it in (cat["evidence_catalog"] or [])
                              if it["id"] == row["hook"].get("evidence_id")), None)
    return {"stage": row["funding_stage"], "industry": row["industry_tags"] or [], "fit": row["fit_score"],
            "role": role_key(row["poc_title"]), "hook_group": hook_group(catalog_field)}


def capture(kind: str, source: str, company_id: int | None = None, email_id: int | None = None,
            rating: int | None = None, tags: list[str] | None = None, note: str | None = None,
            payload: dict | None = None) -> int | None:
    """Record one signal. Never raises: losing a feedback event must not break the action that caused it."""
    try:
        ctx = _context(company_id, email_id)
        with db.connect() as conn:
            return conn.execute(
                """INSERT INTO feedback_events (company_id, email_id, source, kind, rating, tags, note, payload, context)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (company_id, email_id, source, kind, rating, tags or [], note, Jsonb(payload or {}), Jsonb(ctx)),
            ).fetchone()["id"]
    except Exception as e:  # noqa: BLE001
        log.warning("feedback capture failed (%s): %s", kind, e)
        return None


def backfill() -> int:
    """Capture real signals that happened before the loop existed: sent emails and human decisions."""
    n = 0
    with db.connect() as conn:
        sent = conn.execute(
            """SELECT e.id, e.company_id, e.original_body, e.sent_body, e.subject, e.sent_subject, e.edit_distance
               FROM emails e WHERE e.status='sent' AND NOT EXISTS
                 (SELECT 1 FROM feedback_events f WHERE f.email_id=e.id AND f.kind='draft_sent')""").fetchall()
        human = conn.execute(
            """SELECT d.id, d.company_id, d.gate, d.value, d.reason FROM decisions d
               WHERE d.decided_by='human' AND NOT EXISTS
                 (SELECT 1 FROM feedback_events f WHERE f.payload->>'decision_id' = d.id::text)""").fetchall()
    for e in sent:
        capture("draft_sent", "reviewer", e["company_id"], e["id"],
                payload={"edit_rate": e["edit_distance"] or 0.0, "original_body": e["original_body"],
                         "sent_body": e["sent_body"], "original_subject": e["subject"],
                         "sent_subject": e["sent_subject"], "backfilled": True})
        n += 1
    kinds = {"domain": "domain_provided", "review": "review_approved", "hook": "hook_switch"}
    for d in human:
        v = d["value"] or {}
        kind = {"qualified": "override_qualify", "dropped": "override_drop"}.get(v.get("to")) \
            if d["gate"] == "status" else kinds.get(d["gate"])
        if kind:
            capture(kind, "reviewer", d["company_id"], note=d["reason"],
                    payload={**v, "decision_id": d["id"], "backfilled": True})
            n += 1
    return n


# ---------------------------------------------------------------- STORE

class Lesson(BaseModel):
    lesson: str
    kind: Literal["style", "avoid_phrase", "prefer_phrase"]
    key: str
    role: Literal["engineering", "security", "executive", "any"]
    evidence_event_ids: list[int]


class Lessons(BaseModel):
    lessons: list[Lesson]


LESSON_SYSTEM = """You review how a human sales reviewer edited AI-written cold emails before sending them,
plus any ratings and notes they gave. Extract general, reusable writing lessons (not company-specific facts).

Rules:
- Only propose a lesson that the edits or notes clearly show. Prefer fewer, sharper lessons.
- lesson: one imperative sentence the email writer can follow ("Do not say 'audit fire drills'; reviewers cut it").
- kind: avoid_phrase / prefer_phrase when it is about specific wording (put that wording in key), else style.
- key: short lowercase dedupe key, e.g. "avoid:audit fire drills" or "style:shorter opening".
- role: the PoC role the lesson is specific to, or "any".
- evidence_event_ids: the event ids that support it."""


def _upsert_learning(conn, scope: str, kind: str, key: str, lesson: str, applies_to: dict, value: float | None,
                     support: float, confidence: float, evidence: list[int], replace_support: bool) -> None:
    conn.execute(
        f"""INSERT INTO learnings (scope, kind, key, lesson, applies_to, value, support, confidence, evidence_event_ids)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (scope, key) DO UPDATE SET
              lesson = EXCLUDED.lesson, applies_to = EXCLUDED.applies_to, value = EXCLUDED.value,
              support = {'EXCLUDED.support' if replace_support else 'learnings.support + EXCLUDED.support'},
              confidence = EXCLUDED.confidence,
              evidence_event_ids = (SELECT array_agg(DISTINCT x) FROM unnest(learnings.evidence_event_ids
                                    || EXCLUDED.evidence_event_ids) x),
              updated_at = now()""",
        (scope, kind, key, lesson, Jsonb(applies_to), value, int(round(support)), confidence, evidence))


def learn(use_llm: bool = True) -> dict[str, Any]:
    """Distil feedback_events into learnings. Statistical learnings are recomputed from all events
    (so they stay consistent); LLM lessons are drawn only from events not processed before."""
    from pipeline.llm import generate_json

    summary: Counter = Counter()
    with db.connect() as conn:
        sends = conn.execute(
            """SELECT id, payload, context, processed_at FROM feedback_events
               WHERE kind='draft_sent' ORDER BY id""").fetchall()
        ratings = conn.execute(
            """SELECT id, email_id, rating, tags, note, context, processed_at FROM feedback_events
               WHERE kind='draft_rating' ORDER BY id""").fetchall()
        outcomes = conn.execute(
            "SELECT id, payload, context FROM feedback_events WHERE kind='outcome' ORDER BY id").fetchall()
        switches = conn.execute(
            "SELECT id, payload FROM feedback_events WHERE kind='hook_switch' ORDER BY id").fetchall()
        overrides = conn.execute(
            """SELECT id, kind, context FROM feedback_events
               WHERE kind IN ('override_qualify','override_drop') ORDER BY id""").fetchall()

    with db.connect() as conn:
        # writer: phrases reviewers consistently cut or add (deterministic)
        pairs = [(s["id"], s["payload"].get("original_body", ""), s["payload"].get("sent_body", ""))
                 for s in sends if (s["payload"].get("edit_rate") or 0) > 0.01]
        removed, added = phrase_diffs(pairs)
        for phrase, ids in removed.items():
            if len(set(ids)) >= MIN_PHRASE_SUPPORT:
                _upsert_learning(conn, "writer", "avoid_phrase", f"avoid:{phrase}",
                                 f"Do not use the phrase '{phrase}'; reviewers removed it in {len(set(ids))} sends.",
                                 {}, None, len(set(ids)), min(0.95, 0.5 + 0.1 * len(set(ids))), sorted(set(ids)), True)
                summary["avoid_phrase"] += 1
        for phrase, ids in added.items():
            if len(set(ids)) >= MIN_PHRASE_SUPPORT:
                _upsert_learning(conn, "writer", "prefer_phrase", f"prefer:{phrase}",
                                 f"Reviewers added '{phrase}' in {len(set(ids))} sends; use similar wording.",
                                 {}, None, len(set(ids)), min(0.9, 0.4 + 0.1 * len(set(ids))), sorted(set(ids)), True)
                summary["prefer_phrase"] += 1

        # hook: which kind of hook earns replies (deterministic, smoothed)
        hook_out = [(o["context"].get("hook_group", "other"), o["payload"].get("outcome")) for o in outcomes]
        hook_sw = [(hook_group(s["payload"].get("from_field")), hook_group(s["payload"].get("to_field")))
                   for s in switches]
        for g, st in hook_scores_from(hook_out, hook_sw).items():
            _upsert_learning(conn, "hook", "hook_preference", f"hook:{g}",
                             f"'{g}' hooks score {st['score']:.2f} from {st['support']:g} outcome/switch signals.",
                             {}, st["score"], st["support"], min(0.9, 0.3 + 0.1 * st["support"]),
                             [o["id"] for o in outcomes] + [s["id"] for s in switches], True)
            summary["hook_preference"] += 1

        # ranking: industries reviewers keep dropping or qualifying (deterministic, bounded)
        decisions = [(o["context"].get("industry") or [], 1 if o["kind"] == "override_qualify" else -1)
                     for o in overrides]
        decisions += [(o["context"].get("industry") or [], 1 if o["payload"].get("outcome") in POSITIVE_OUTCOMES
                       else -1) for o in outcomes if o["payload"].get("outcome") in POSITIVE_OUTCOMES | NEGATIVE_OUTCOMES]
        for ind, st in industry_adjust_from(decisions).items():
            if st["support"] >= MIN_INDUSTRY_SUPPORT and st["delta"] != 0:
                _upsert_learning(conn, "ranking", "industry_adjust", f"industry:{ind}",
                                 f"Rank {ind} companies {st['delta']:+.3f} ({st['pos']} positive, {st['neg']} negative "
                                 f"reviewer/prospect signals).", {"industry": ind}, st["delta"], st["support"],
                                 min(0.9, 0.3 + 0.1 * st["support"]), [o["id"] for o in overrides], True)
                summary["industry_adjust"] += 1

    # writer: style lessons from new edits + rating notes (one Gemini summarisation, Python keeps the result)
    new_sends = [s for s in sends if s["processed_at"] is None and (s["payload"].get("edit_rate") or 0) > 0.01]
    new_ratings = [r for r in ratings if r["processed_at"] is None and (r["note"] or r["tags"] or r["rating"])]
    if use_llm and (new_sends or new_ratings):
        blocks = []
        for s in new_sends:
            p = s["payload"]
            blocks.append(f"EVENT {s['id']} (edit, PoC role {s['context'].get('role')}, edit rate {p.get('edit_rate')}):"
                          f"\n--- ORIGINAL ---\n{p.get('original_body')}\n--- SENT ---\n{p.get('sent_body')}")
        for r in new_ratings:
            blocks.append(f"EVENT {r['id']} (rating {r['rating']:+d} on a draft, PoC role {r['context'].get('role')}): "
                          f"tags {r['tags']}; note: {r['note'] or '-'}")
        res = generate_json("\n\n".join(blocks), Lessons, system=LESSON_SYSTEM, stage="learn", use_cache=False)
        valid_ids = {s["id"] for s in new_sends} | {r["id"] for r in new_ratings}
        if res.data:
            with db.connect() as conn:
                for les in res.data.lessons:
                    ev = [i for i in les.evidence_event_ids if i in valid_ids]
                    if not ev:
                        continue          # a lesson with no real supporting event is discarded
                    _upsert_learning(conn, "writer", les.kind, les.key[:120], les.lesson[:400],
                                     {} if les.role == "any" else {"role": les.role}, None, len(ev),
                                     min(0.85, 0.4 + 0.15 * len(ev)), ev, False)
                    summary["llm_lesson"] += 1

    with db.connect() as conn:
        n = conn.execute("UPDATE feedback_events SET processed_at=now() WHERE processed_at IS NULL").rowcount
    summary["events_processed"] = n
    log.info("learn: %s", dict(summary))
    return dict(summary)


# ---------------------------------------------------------------- RETRIEVE

def retrieve(scope: str, ctx: dict | None = None, limit: int = 8) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT * FROM learnings WHERE scope=%s AND status='active'
               ORDER BY support * confidence DESC, updated_at DESC""", (scope,)).fetchall()
    return [r for r in rows if context_matches(r["applies_to"], ctx or {})][:limit]


def hook_preferences() -> dict[str, float]:
    """Learned hook-group scores, only once enough outcomes back them."""
    return {r["key"].split(":", 1)[1]: r["value"] for r in retrieve("hook", limit=50)
            if r["value"] is not None and r["support"] >= MIN_HOOK_SUPPORT}


def industry_adjustments() -> dict[str, dict]:
    return {r["applies_to"].get("industry"): {"delta": r["value"], "id": r["id"]}
            for r in retrieve("ranking", limit=100) if r["value"] is not None}


# ---------------------------------------------------------------- APPLY bookkeeping

def record_applications(learning_ids: list[int], scope: str, company_id: int | None = None,
                        email_id: int | None = None) -> None:
    if not learning_ids:
        return
    with db.connect() as conn:
        for lid in learning_ids:
            conn.execute("""INSERT INTO learning_applications (learning_id, company_id, email_id, scope)
                            VALUES (%s,%s,%s,%s)""", (lid, company_id, email_id, scope))
        conn.execute("""UPDATE learnings SET times_applied = times_applied + 1, last_applied_at = now()
                        WHERE id = ANY(%s)""", (learning_ids,))
