"""Point of contact via Hunter Domain Search: one search per qualified company, never more.

HUNTER_MODE=mock (default) reads fixtures/hunter_mock.json and spends nothing.
HUNTER_MODE=live checks remaining searches on /v2/account (free) first and refuses below 1.
A company that already has a live Hunter response stored is never searched again.
The API key is passed as a query param, so URLs are never logged.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import requests
from psycopg.types.json import Jsonb

from pipeline import db
from pipeline.config import ICP, ROOT, get_icp, get_settings
from pipeline.stage1 import keyword_hits
from pipeline.usage import current_company_id, log_usage

log = logging.getLogger(__name__)

API = "https://api.hunter.io/v2"
MOCK_PATH = ROOT / "fixtures" / "hunter_mock.json"
VERIFICATION_RANK = {"valid": 3, "accept_all": 2, "unknown": 1, None: 1}
UNUSABLE_VERIFICATION = {"invalid", "disposable"}


# ---------------------------------------------------------------- picking (pure)

@dataclass
class Pick:
    email: dict[str, Any]
    rule: str
    reason: str


def title_rule(position: str | None, rules: list[str], icp: ICP) -> tuple[int, str] | None:
    """Index + name of the highest-priority poc rule this job title satisfies."""
    if not position:
        return None
    for i, rule in enumerate(rules):
        if keyword_hits(position, icp.poc_title_aliases.get(rule, [rule.lower()])):
            return i, rule
    return None


def pick_contact(emails: list[dict], stage: str | None, icp: ICP) -> tuple[Pick | None, list[dict]]:
    """Choose by poc_rules[stage] priority, then verification status, then confidence.
    Returns the pick and an audit list of every email considered with why it was or wasn't eligible."""
    rules = icp.poc_rules.get(stage or "", [])
    considered, ranked = [], []
    for e in emails:
        name = " ".join(filter(None, [e.get("first_name"), e.get("last_name")])) or None
        row = {"email": e.get("value"), "name": name, "position": e.get("position"),
               "confidence": e.get("confidence"), "verification": (e.get("verification") or {}).get("status")}
        match = title_rule(e.get("position"), rules, icp)
        if e.get("type") != "personal" or not e.get("first_name"):
            row["skip"] = "generic mailbox"
        elif row["verification"] in UNUSABLE_VERIFICATION:
            row["skip"] = f"verification {row['verification']}"
        elif (e.get("confidence") or 0) < icp.hunter_min_confidence:
            row["skip"] = f"confidence {e.get('confidence')} < {icp.hunter_min_confidence}"
        elif not match:
            row["skip"] = f"title not in poc_rules for {stage}"
        else:
            row["rule"] = match[1]
            ranked.append(((match[0], -VERIFICATION_RANK.get(row["verification"], 0), -(e.get("confidence") or 0)),
                           match[1], e))
        considered.append(row)
    if not ranked:
        return None, considered
    _, rule, best = sorted(ranked, key=lambda x: x[0])[0]
    ver = (best.get("verification") or {}).get("status")
    reason = (f"{stage} rule order {rules}: '{best.get('position')}' matches '{rule}' "
              f"(rank {rules.index(rule) + 1} of {len(rules)}); email {ver}, confidence {best.get('confidence')}")
    return Pick(best, rule, reason), considered


# ---------------------------------------------------------------- Hunter I/O

def _get(path: str, params: dict[str, str]) -> tuple[dict | None, str | None]:
    key = get_settings().hunter_api_key
    if not key:
        return None, "HUNTER_API_KEY not set"
    err = None
    for attempt in range(3):
        try:
            r = requests.get(f"{API}/{path}", params={**params, "api_key": key}, timeout=15)
            if r.ok:
                return r.json(), None
            err = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        except requests.RequestException as e:
            err = type(e).__name__          # never include the URL (it holds the key)
        if attempt < 2:
            time.sleep(1.5 * 2 ** attempt)
    return None, err


def credits_remaining(live: bool) -> int | None:
    """Live: ask /v2/account (free). Mock: monthly allowance minus live searches logged this month."""
    icp = get_icp()
    if live:
        data, err = _get("account", {})
        if err or not data:
            log.warning("Hunter account check failed: %s", err)
            return None
        try:
            s = data["data"]["requests"]["searches"]
            return int(s["available"]) - int(s["used"])
        except (KeyError, TypeError, ValueError):
            log.warning("unexpected Hunter account payload")
            return None
    with db.connect() as conn:
        used = conn.execute(
            """SELECT coalesce(sum(credits_used), 0) n FROM api_usage
               WHERE service='hunter' AND NOT cached AND ok AND ts >= date_trunc('month', now())""").fetchone()["n"]
    return icp.hunter_monthly_searches - int(used)


def domain_search(domain: str, org: str, live: bool) -> tuple[dict | None, str | None]:
    if not live:
        text = MOCK_PATH.read_text(encoding="utf-8").replace("{domain}", domain).replace("{org}", org)
        return json.loads(text), None
    return _get("domain-search", {"domain": domain, **get_icp().hunter_search_params})


# ---------------------------------------------------------------- per company

def _decision(cid: int, run_id: int | None, result: str, value: Any, quote: str | None, url: str | None,
              reason: str) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM decisions WHERE company_id=%s AND stage='poc' AND decided_by='rule'", (cid,))
        conn.execute(
            """INSERT INTO decisions (company_id, run_id, stage, gate, result, value, evidence_quote, source_url,
                                      reason) VALUES (%s,%s,'poc','contact',%s,%s,%s,%s,%s)""",
            (cid, run_id, result, Jsonb(value), quote, url, reason))


def _flag(cid: int, reason: str) -> str:
    with db.connect() as conn:
        conn.execute("""UPDATE companies SET status='flagged', needs_review=true, flag_reason=%s, updated_at=now()
                        WHERE id=%s""", (reason, cid))
    log.info("  -> PoC FLAGGED: %s", reason)
    return "flagged"


def find_poc(company: dict, run_id: int | None = None) -> str:
    """Pick and store a contact for a qualified company. Returns the company's resulting status."""
    icp, settings = get_icp(), get_settings()
    live = settings.hunter_mode == "live"
    cid, domain = company["id"], company["domain"]
    current_company_id.set(cid)

    with db.connect() as conn:
        stored = conn.execute(
            "SELECT hunter_raw FROM contacts WHERE company_id=%s AND NOT is_mock ORDER BY id DESC LIMIT 1",
            (cid,)).fetchone()
    if stored:
        raw, err, reused = stored["hunter_raw"], None, True
        log.info("  reusing stored live Hunter response for %s (no credit spent)", domain)
    else:
        reused = False
        remaining = credits_remaining(live)
        if live and (remaining is None or remaining < 1):
            return _flag(cid, f"Hunter not called: {remaining if remaining is not None else 'unknown'} searches left")
        raw, err = domain_search(domain, company["name"], live)
        log_usage("hunter", stage="poc", model="live" if live else "mock", ok=err is None, error=err,
                  credits_used=1 if live and err is None else 0, cached=not live,
                  credits_remaining=(remaining - 1) if (live and remaining is not None and err is None) else remaining)
    if err or not raw:
        return _flag(cid, f"Hunter error: {err}")

    emails = (raw.get("data") or {}).get("emails") or []
    pick, considered = pick_contact(emails, company.get("funding_stage"), icp)
    if not pick:
        _decision(cid, run_id, "fail", {"considered": considered}, None, None,
                  f"Hunter returned {len(emails)} email(s); none fit poc_rules for {company.get('funding_stage')}")
        return _flag(cid, f"no matching PoC among {len(emails)} Hunter emails")

    e = pick.email
    src = next((s.get("uri") for s in e.get("sources") or [] if s.get("uri")), None)
    with db.connect() as conn:
        conn.execute("DELETE FROM contacts WHERE company_id=%s", (cid,))   # raw response is kept in the new row
        conn.execute(
            """INSERT INTO contacts (company_id, run_id, full_name, first_name, title, email, confidence,
                                     verification_status, seniority, department, pick_rule, pick_reason,
                                     hunter_raw, is_mock)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (cid, run_id, " ".join(filter(None, [e.get("first_name"), e.get("last_name")])), e.get("first_name"),
             e.get("position"), e.get("value"), e.get("confidence"), (e.get("verification") or {}).get("status"),
             e.get("seniority"), e.get("department"), pick.rule, pick.reason, Jsonb(raw), not live and not reused))
    _decision(cid, run_id, "pass",
              {"name": f"{e.get('first_name')} {e.get('last_name')}", "title": e.get("position"),
               "email": e.get("value"), "rule": pick.rule, "mock": not live and not reused,
               "considered": considered},
              e.get("position"), src, pick.reason)
    log.info("  -> PoC %s %s, %s <%s>%s", e.get("first_name"), e.get("last_name"), e.get("position"),
             e.get("value"), " [MOCK]" if not live and not reused else "")
    return company.get("status") or "qualified"


def run_pending(run_id: int | None = None) -> list[dict]:
    """Find PoCs for every qualified company that has no contact yet."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT c.* FROM companies c WHERE c.status='qualified'
               AND NOT EXISTS (SELECT 1 FROM contacts k WHERE k.company_id=c.id)
               ORDER BY c.fit_score DESC NULLS LAST""").fetchall()
    out = []
    for c in rows:
        log.info("=== PoC for %s (%s, %s)", c["name"], c["domain"], c["funding_stage"])
        try:
            status = find_poc(c, run_id)
        except Exception as e:  # noqa: BLE001
            log.exception("PoC failed for %s", c["name"])
            status = _flag(c["id"], f"PoC error: {type(e).__name__}")
        out.append({"company": c["name"], "status": status})
    return out
