"""Human decisions from the dashboard. Each one is written to `decisions` (decided_by='human')
so the drill-down shows who overrode what and why."""
from __future__ import annotations

import logging
from typing import Any

from psycopg.types.json import Jsonb

from pipeline import db
from pipeline.stage1 import normalise_domain

log = logging.getLogger(__name__)


def _company(cid: int) -> dict:
    with db.connect() as conn:
        return conn.execute("SELECT * FROM companies WHERE id=%s", (cid,)).fetchone()


def _human_decision(cid: int, gate: str, value: Any, reason: str) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO decisions (company_id, run_id, stage, gate, result, value, reason, decided_by)
               VALUES (%s, (SELECT last_run_id FROM companies WHERE id=%s), 'human', %s, 'override', %s, %s, 'human')""",
            (cid, cid, gate, Jsonb(value), reason))


def _has(table: str, cid: int) -> bool:
    with db.connect() as conn:
        return conn.execute(f"SELECT 1 FROM {table} WHERE company_id=%s LIMIT 1", (cid,)).fetchone() is not None


def qualify(cid: int, note: str = "") -> str:
    """Override to qualified, then find a PoC and write a draft if they don't exist yet."""
    from pipeline.poc import find_poc
    from pipeline.write import run_pending as write_drafts

    c = _company(cid)
    if not c["domain"]:
        return "Needs a domain first: use Provide domain."
    _human_decision(cid, "status", {"from": c["status"], "to": "qualified"}, note or "qualified by reviewer")
    with db.connect() as conn:
        conn.execute("""UPDATE companies SET status='qualified', needs_review=false, flag_reason=NULL,
                        drop_reason=NULL, updated_at=now() WHERE id=%s""", (cid,))
    msgs = ["Qualified."]
    if not _has("contacts", cid):
        status = find_poc({**c, "status": "qualified"}, c["last_run_id"])
        msgs.append(f"PoC: {status}.")
        if status != "qualified":
            return " ".join(msgs)
    if not _has("emails", cid):
        res = write_drafts(c["last_run_id"], company_id=cid)
        msgs.append(f"Draft: {res[0]['status'] if res else 'none'}.")
    return " ".join(msgs)


def approve_review(cid: int, note: str = "") -> str:
    """Keep the company qualified and clear the review flag (e.g. reviewer is happy with the default hook)."""
    _human_decision(cid, "review", {"needs_review": False}, note or "reviewed and approved as is")
    with db.connect() as conn:
        conn.execute("UPDATE companies SET needs_review=false, updated_at=now() WHERE id=%s", (cid,))
    return "Review cleared."


def drop(cid: int, reason: str) -> str:
    c = _company(cid)
    reason = reason.strip() or "dropped by reviewer"
    _human_decision(cid, "status", {"from": c["status"], "to": "dropped"}, reason)
    with db.connect() as conn:
        conn.execute("""UPDATE companies SET status='dropped', needs_review=false, drop_reason=%s, updated_at=now()
                        WHERE id=%s""", (f"human: {reason}", cid))
        conn.execute("UPDATE emails SET status='discarded' WHERE company_id=%s AND status IN ('drafted','flagged')",
                     (cid,))
    return "Dropped."


def provide_domain(cid: int, raw_domain: str) -> str:
    """Store a human-supplied domain and research the company now (crawl, extract, qualify, PoC, draft)."""
    from pipeline.batch import research_company

    domain = normalise_domain(raw_domain)
    if not domain or "." not in domain:
        return f"'{raw_domain}' is not a domain."
    c = _company(cid)
    _human_decision(cid, "domain", {"domain": domain}, "domain provided by reviewer")
    with db.connect() as conn:
        conn.execute("""UPDATE companies SET domain=%s, domain_source='human', updated_at=now() WHERE id=%s""",
                     (domain, cid))
        run_id = c["last_run_id"] or conn.execute("SELECT max(id) m FROM runs").fetchone()["m"]
    status = research_company({**c, "domain": domain}, run_id, c["research_batch"])
    c = _company(cid)
    return f"Researched {domain}: {status}" + (f" ({c['flag_reason'] or c['drop_reason']})"
                                               if (c["flag_reason"] or c["drop_reason"]) else "")


def choose_hook(cid: int, evidence_id: str) -> str:
    """Switch the email hook to another dated evidence item and regenerate the draft."""
    from pipeline.write import run_pending as write_drafts

    _human_decision(cid, "hook", {"evidence_id": evidence_id}, "hook chosen by reviewer")
    with db.connect() as conn:
        conn.execute("UPDATE companies SET chosen_hook=%s, needs_review=false, updated_at=now() WHERE id=%s",
                     (Jsonb({"evidence_id": evidence_id, "by": "human"}), cid))
    res = write_drafts(company_id=cid)
    return f"Regenerated with hook {evidence_id}: {res[0]['status'] if res else 'no contact'}"
