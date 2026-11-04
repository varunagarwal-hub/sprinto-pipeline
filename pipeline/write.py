"""Pain points + cold email. Python builds a numbered evidence catalog and picks the hook;
Gemini writes; a code validator checks every company-specific claim maps to the catalog.
Fail -> regenerate once with the errors as feedback -> still failing -> flag."""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from pipeline import db
from pipeline.config import ROOT, ICP, get_icp, get_settings
from pipeline.llm import generate_json
from pipeline.qualify import parse_date
from pipeline.stage1 import keyword_hits
from pipeline.usage import current_company_id, current_stage

log = logging.getLogger(__name__)
SPRINTO_MD = ROOT / "sprinto.md"
MAX_WORDS = 120


# ---------------------------------------------------------------- evidence catalog + hook (pure)

def build_catalog(company: dict, evidence: dict) -> list[dict]:
    items: list[dict] = []

    def add(field: str, fact: str, quote: str | None, url: str | None, date_: str | None) -> None:
        items.append({"id": f"E{len(items) + 1}", "field": field, "fact": fact, "quote": quote,
                      "url": url, "date": date_})

    fund = next((g for g in company.get("stage1_evidence") or [] if g.get("gate") == "funding_stage"), None)
    if fund:
        amount = company.get("funding_amount")
        add("funding", f"Raised {amount + ' ' if amount else ''}{company.get('funding_stage')}".strip(),
            fund.get("headline"), fund.get("url"), fund.get("date"))
    for s in evidence.get("audit_signals", []):
        add(f"audit_signal:{s['type']}", s["text"], s["text"], s["url"], s.get("date"))
    for c in evidence.get("certifications_held", []):
        add("certification", f"{c['name']} ({c['status']})", c["evidence_quote"], c["source_url"], c.get("date"))
    for f, label in [("product_summary", "Product"), ("enterprise_customers", "Customers include"),
                     ("customer_count_claim", "Customer count"), ("industry", "Industry"),
                     ("cloud_provider", "Cloud"), ("employee_count", "Employees")]:
        fact = evidence.get(f)
        if fact:
            v = ", ".join(fact["value"]) if isinstance(fact["value"], list) else fact["value"]
            add(f, f"{label}: {v}", fact["evidence_quote"], fact["source_url"], fact.get("date"))
    return items


def hook_candidates(catalog: list[dict], icp: ICP, today: date) -> list[dict]:
    """Dated items only (the hook must be dated). Fresh audit signals first, then the funding round,
    then anything else dated, newest first within each group."""
    def group(it: dict) -> int:
        d = parse_date(it["date"])
        fresh = d is not None and (today - d).days <= icp.audit_signal_max_age_days
        if it["field"].startswith("audit_signal") and fresh:
            return 0
        if it["field"] == "funding":
            return 1
        return 2
    dated = [it for it in catalog if parse_date(it["date"])]
    return sorted(dated, key=lambda it: (group(it), -parse_date(it["date"]).toordinal()))


def role_angle(title: str | None) -> str:
    t = (title or "").lower()
    if keyword_hits(t, ["ciso", "security", "compliance", "grc", "risk"]):
        return "security leader: talk control coverage, continuous monitoring and audit readiness across frameworks"
    return "engineering leader: talk engineering hours saved, no audit fire drills, sales not blocked by security reviews"


# ---------------------------------------------------------------- writer

class CitedText(BaseModel):
    text: str
    evidence_id: str


class EmailDraft(BaseModel):
    pain_points: list[CitedText]
    hook: CitedText
    subject: str
    body: str
    claims: list[CitedText]


SYSTEM = f"""You write one short cold email from Sprinto to a named person at a SaaS company.

Rules:
1. Address the person by first name on the first line ("Hi <first name>,"). Plain text only: no markdown, no bullet symbols, no links.
2. The body is under {MAX_WORDS} words including the greeting and sign-off.
3. Open with the HOOK evidence item you are given: say what happened and when. An evidence date is when the news or post was published, not necessarily when the event happened, so write "announced in October" / "posted in October", never "on 18 October". Never open with generic flattery.
4. Every fact about the prospect company (events, numbers, customers, certifications, roles, products) must come from the EVIDENCE list. Do not invent numbers, customers, certifications, dates or names. If unsure, leave it out.
5. Claims about Sprinto may only come from the SPRINTO VALUE PROPS text.
6. Exactly one ask: a 15-minute call. Only one question mark in the whole email.
7. Match the tone to the role angle given.
8. Sign off with the sender name given.

Output:
- pain_points: exactly 2, each a short sentence about a compliance pain this company likely has, each citing the evidence_id that suggests it.
- hook: the opening observation, citing the HOOK evidence_id.
- subject: under 8 words, specific, no brackets, no emojis.
- body: the email.
- claims: every company-specific statement you made in the body, each with the evidence_id it comes from."""


def build_prompt(company: dict, contact: dict, catalog: list[dict], hook_id: str, sprinto_text: str,
                 sender: str, feedback: list[str] | None = None) -> str:
    parts = [
        f"PROSPECT: {contact['first_name']} ({contact['full_name']}), {contact['title']} at {company['name']} "
        f"({company['domain']})",
        f"ROLE ANGLE: {role_angle(contact.get('title'))}",
        f"SENDER NAME: {sender}",
        f"HOOK: {hook_id}",
        "EVIDENCE:\n" + json.dumps(catalog, indent=1, ensure_ascii=False),
        "SPRINTO VALUE PROPS:\n" + sprinto_text,
    ]
    if feedback:
        parts.append("YOUR PREVIOUS DRAFT FAILED THESE CHECKS. Fix every one:\n- " + "\n- ".join(feedback))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- validator (pure)

ALWAYS_OK_CAPS = {
    "i", "hi", "hello", "hey", "best", "thanks", "thank", "cheers", "regards", "sprinto", "soc", "iso", "gdpr",
    "hipaa", "pci", "dss", "type", "ii", "ai", "saas", "cto", "ciso", "vp", "ceo", "grc", "api", "us", "eu",
    "monday", "tuesday", "wednesday", "thursday", "friday", "january", "february", "march", "april", "may",
    "june", "july", "august", "september", "october", "november", "december", "jan", "feb", "mar", "apr",
    "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "series", "would", "worth", "open", "quick",
}
_MARKDOWN = re.compile(r"\*\*|__|^\s*#|\[[^\]]+\]\([^)]+\)|^\s*[-*•]\s", re.M)


def _corpus(catalog: list[dict], company: dict, contact: dict, sprinto_text: str, sender: str) -> str:
    bits = [company["name"], company.get("domain") or "", contact.get("full_name") or "", contact.get("title") or "",
            sprinto_text, sender]
    for it in catalog:
        bits += [it["fact"] or "", it["quote"] or "", it["date"] or ""]
    return " ".join(bits).lower()


def ungrounded_terms(body: str, corpus: str) -> tuple[list[str], list[str]]:
    """Numbers and capitalised names in the body that appear nowhere in the evidence/value-prop corpus."""
    lines = [l for l in body.splitlines() if l.strip()]
    text = " ".join(lines[1:]) if len(lines) > 1 else body       # greeting line holds the first name
    nums = [n.rstrip(".,") for n in re.findall(r"\d[\d,.]*", text)]
    bad_nums = sorted({n for n in nums if n != "15" and n.replace(",", "") not in corpus.replace(",", "")})
    names: set[str] = set()
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        words = sentence.split()
        for w in words[1:]:
            w = re.sub(r"[’']s$", "", w.strip(",.;:!?()\"“”"))
            if w and w[0].isupper() and w.lower() not in ALWAYS_OK_CAPS and w.lower() not in corpus:
                names.add(w)
    return bad_nums, sorted(names)


def validate(draft: EmailDraft, catalog: list[dict], hook_id: str, company: dict, contact: dict,
             sprinto_text: str, sender: str) -> list[str]:
    errs: list[str] = []
    ids = {it["id"] for it in catalog}
    body = draft.body.strip()
    if len(draft.pain_points) != 2:
        errs.append(f"pain_points must be exactly 2 (got {len(draft.pain_points)})")
    for p in draft.pain_points:
        if p.evidence_id not in ids:
            errs.append(f"pain point cites unknown evidence id {p.evidence_id}")
    if draft.hook.evidence_id != hook_id:
        errs.append(f"hook must cite {hook_id} (cited {draft.hook.evidence_id})")
    for c in draft.claims:
        if c.evidence_id not in ids:
            errs.append(f"claim '{c.text[:60]}' cites unknown evidence id {c.evidence_id}")
    first_line = body.splitlines()[0] if body else ""
    if contact["first_name"].lower() not in first_line.lower():
        errs.append(f"first line must greet {contact['first_name']} by first name")
    n_words = len(body.split())
    if n_words >= MAX_WORDS:
        errs.append(f"body is {n_words} words; must be under {MAX_WORDS}")
    if body.count("?") > 1:
        errs.append("more than one question mark: keep exactly one ask")
    if not re.search(r"\b15[- ]?min", body, re.I):
        errs.append("missing the single ask for a 15-minute call")
    if _MARKDOWN.search(body):
        errs.append("body must be plain text (no markdown, bullets or links)")
    if not (2 <= len(draft.subject.split()) <= 10) or "[" in draft.subject:
        errs.append("subject must be 2-10 words with no brackets")
    hook = next((it for it in catalog if it["id"] == hook_id), None)
    if hook:
        opening = " ".join(body.splitlines()[1:4]).lower()
        hook_words = {w[:5] for w in re.findall(r"[a-z]{5,}", (hook["quote"] or hook["fact"]).lower())}
        if not hook_words & {w[:5] for w in re.findall(r"[a-z]{5,}", opening)}:
            errs.append(f"the opening lines must reference the hook ({hook['fact'][:80]})")
    bad_nums, bad_names = ungrounded_terms(body, _corpus(catalog, company, contact, sprinto_text, sender))
    if bad_nums:
        errs.append(f"numbers not found in evidence: {bad_nums}")
    if bad_names:
        errs.append(f"names/terms not found in evidence: {bad_names}")
    return errs


# ---------------------------------------------------------------- I/O

def generate_for_company(company: dict, contact: dict, run_id: int | None = None) -> str:
    icp, settings = get_icp(), get_settings()
    cid = company["id"]
    current_company_id.set(cid)
    current_stage.set("write")
    sender = settings.sender_name or "The Sprinto team"
    sprinto_text = SPRINTO_MD.read_text(encoding="utf-8")
    catalog = build_catalog(company, company["evidence"] or {})
    candidates = hook_candidates(catalog, icp, date.today())
    chosen = (company.get("chosen_hook") or {}).get("evidence_id")
    hook = next((it for it in catalog if it["id"] == chosen), None) or (candidates[0] if candidates else None)
    if not hook:
        _flag(cid, "no dated evidence available for a hook")
        return "flagged"

    feedback: list[str] | None = None
    log_: list[dict] = []
    draft = None
    for attempt in (1, 2):
        prompt = build_prompt(company, contact, catalog, hook["id"], sprinto_text, sender, feedback)
        res = generate_json(prompt, EmailDraft, system=SYSTEM, stage="write", temperature=0.4)
        if res.data is None:
            log_.append({"attempt": attempt, "errors": [f"Gemini error: {res.error}"]})
            feedback = None
            continue
        draft = res.data
        errs = validate(draft, catalog, hook["id"], company, contact, sprinto_text, sender)
        log_.append({"attempt": attempt, "errors": errs, "tokens_in": res.tokens_in, "tokens_out": res.tokens_out})
        if not errs:
            break
        feedback = errs
        log.info("  draft attempt %d failed validation: %s", attempt, errs)

    ok = draft is not None and not log_[-1]["errors"]
    with db.connect() as conn:
        conn.execute("DELETE FROM emails WHERE company_id=%s AND status IN ('drafted','flagged')", (cid,))
        conn.execute(
            """INSERT INTO emails (company_id, contact_id, run_id, pain_points, hook, claims, subject, original_body,
                                   validator_log, attempts, status, evidence_catalog, hook_candidates)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (cid, contact["id"], run_id,
             Jsonb([p.model_dump() for p in draft.pain_points]) if draft else None,
             Jsonb({**(draft.hook.model_dump() if draft else {}), "evidence_id": hook["id"], "url": hook["url"],
                    "date": hook["date"], "quote": hook["quote"]}),
             Jsonb([c.model_dump() for c in draft.claims]) if draft else None,
             draft.subject if draft else None, draft.body if draft else None,
             Jsonb(log_), len(log_), "drafted" if ok else "flagged", Jsonb(catalog), Jsonb(candidates)))
        conn.execute("UPDATE companies SET status=%s, updated_at=now() WHERE id=%s",
                     ("drafted" if ok else "qualified", cid))
    if not ok:
        _flag(cid, f"email failed validation twice: {log_[-1]['errors'][:3]}")
        return "flagged"
    log.info("  -> drafted (%d attempt%s), hook %s: %s", len(log_), "" if len(log_) == 1 else "s",
             hook["id"], hook["fact"][:70])
    return "drafted"


def _flag(cid: int, reason: str) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE companies SET needs_review=true, flag_reason=%s, updated_at=now() WHERE id=%s",
                     (reason, cid))
    log.info("  -> email FLAGGED: %s", reason)


def run_pending(run_id: int | None = None, company_id: int | None = None) -> list[dict]:
    """Write drafts for companies that have a contact but no draft yet (or one company, to regenerate)."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT c.*, row_to_json(k.*) AS contact FROM companies c
               JOIN LATERAL (SELECT * FROM contacts WHERE company_id=c.id ORDER BY id DESC LIMIT 1) k ON true
               WHERE (%s::bigint IS NULL AND c.status='qualified'
                      AND NOT EXISTS (SELECT 1 FROM emails e WHERE e.company_id=c.id))
                  OR c.id = %s::bigint
               ORDER BY c.fit_score DESC NULLS LAST""", (company_id, company_id)).fetchall()
    out = []
    for r in rows:
        log.info("=== Email for %s -> %s, %s", r["name"], r["contact"]["full_name"], r["contact"]["title"])
        try:
            status = generate_for_company(r, r["contact"], run_id)
        except Exception as e:  # noqa: BLE001
            log.exception("writer failed for %s", r["name"])
            _flag(r["id"], f"writer error: {type(e).__name__}")
            status = "flagged"
        out.append({"company": r["name"], "status": status})
    return out
