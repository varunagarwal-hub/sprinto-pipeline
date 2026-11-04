"""Qualifier: Python rules decide, Gemini only supplied the evidence.

Pure functions (evaluate_gates, audit_status, decide, fit_score, next_page_type) are unit-tested.
qualify_company() runs the agent loop: while a gate or the audit signal is unknown, fetch the page
most likely to answer it, re-extract, merge, re-check (max max_refetches_per_company).
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from pipeline.config import ICP, get_icp
from pipeline.extract import extract, merge
from pipeline.scraper import Page, Scraper
from pipeline.stage1 import keyword_hits

log = logging.getLogger(__name__)

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"
HIRE_TYPES = {"compliance_hire", "security_hire"}
# Gate name -> evidence field the agent loop should chase (refetch_map keys), in priority order.
CHASE_ORDER = [("is_saas", "is_saas"), ("competitor_trust_vendor", "trust_center_vendor"),
               ("certifications", "certifications_held"), ("audit_signal", "audit_signals"),
               ("employee_count", "employee_count")]


@dataclass
class Gate:
    gate: str
    result: str
    value: Any = None
    evidence_quote: str | None = None
    source_url: str | None = None
    evidence_date: str | None = None
    reason: str | None = None


@dataclass
class Outcome:
    status: str                      # qualified | flagged | dropped
    needs_review: bool
    reason: str | None
    unknown_fields: list[str]
    gates: list[Gate]
    fit_score: float | None = None
    fit_breakdown: dict | None = None
    iterations: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------- helpers

def _fact_gate(name: str, result: str, fact: dict | None, value: Any = None, reason: str | None = None) -> Gate:
    fact = fact or {}
    return Gate(name, result, value if value is not None else fact.get("value"), fact.get("evidence_quote"),
                fact.get("source_url"), fact.get("date"), reason)


def page_types(evidence: dict) -> set[str]:
    return {p["type"] for p in (evidence.get("_meta") or {}).get("pages", [])}


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s[:7], fmt).date()
        except ValueError:
            continue
    return None


def held_certs(evidence: dict, icp: ICP) -> list[dict]:
    return [c for c in evidence.get("certifications_held", [])
            if c["status"] == "held" and c["name"] in icp.certifications]


# ---------------------------------------------------------------- gates

def gate_saas(ev: dict) -> Gate:
    f = ev.get("is_saas")
    if f is None:
        return _fact_gate("is_saas", UNKNOWN, None, reason="no statement on the site establishes a SaaS product")
    return _fact_gate("is_saas", PASS if f["value"] else FAIL, f,
                      reason=None if f["value"] else "site describes a non-SaaS business")


def gate_funding(company: dict, icp: ICP) -> Gate:
    stage = company.get("funding_stage")
    s1 = next((g for g in company.get("stage1_evidence") or [] if g.get("gate") == "funding_stage"), {})
    if not stage:
        return Gate("funding_stage", UNKNOWN, None, reason="no funding stage on record (manual domain without stage)")
    ok = stage in icp.funding_stages
    return Gate("funding_stage", PASS if ok else FAIL, stage, s1.get("headline"), s1.get("url"), s1.get("date"),
                None if ok else f"{stage} outside {icp.funding_stages}")


def gate_certifications(ev: dict, icp: ICP) -> Gate:
    held = held_certs(ev, icp)
    names = sorted({c["name"] for c in held})
    first = held[0] if held else {}
    if set(icp.certifications) <= set(names):
        return Gate("certifications", FAIL, names, first.get("evidence_quote"), first.get("source_url"),
                    first.get("date"), "already holds SOC 2, ISO 27001 and GDPR")
    looked = page_types(ev) & {"trust", "security"}
    if held or looked:
        return Gate("certifications", PASS, names, first.get("evidence_quote"), first.get("source_url"),
                    first.get("date"), f"holds {names or 'none of the target frameworks'}"
                    + ("" if held else f" (checked {sorted(looked)} pages)"))
    return Gate("certifications", UNKNOWN, names, reason="no trust/security page read yet")


def gate_competitor(ev: dict, icp: ICP) -> Gate:
    meta = ev.get("_meta") or {}
    type_by_url = {p["url"]: p["type"] for p in meta.get("pages", [])}
    competitors = {v.lower() for v in icp.competitor_trust_vendors}
    hits = [h for h in meta.get("trust_vendor_html", [])
            if type_by_url.get(h["url"]) in ("trust", "security") or "trust" in h["snippet"].lower()]
    for h in hits:
        if h["vendor"].lower() == "sprinto":
            return Gate("competitor_trust_vendor", FAIL, h["vendor"], h["snippet"], h["url"], None,
                        "trust center already runs on Sprinto: existing customer, do not cold-pitch")
    for h in hits:
        if h["vendor"].lower() in competitors:
            return Gate("competitor_trust_vendor", FAIL, h["vendor"], h["snippet"], h["url"], None,
                        f"trust center runs on {h['vendor']} (found '{h['matched']}' in page HTML)")
    llm = ev.get("trust_center_vendor")
    if llm and any(c in llm["value"].lower() for c in competitors):
        return _fact_gate("competitor_trust_vendor", FAIL, llm, reason=f"trust center runs on {llm['value']}")
    if hits:
        h = hits[0]
        return Gate("competitor_trust_vendor", PASS, h["vendor"], h["snippet"], h["url"], None,
                    f"trust center vendor is {h['vendor']}, not a competitor")
    if page_types(ev) & {"trust", "security"}:
        return Gate("competitor_trust_vendor", PASS, None, reason="trust/security page read; no Vanta/Drata found")
    return Gate("competitor_trust_vendor", UNKNOWN, None, reason="no trust/security page read yet")


def gate_employees(ev: dict, icp: ICP) -> Gate:
    f = ev.get("employee_count")
    if f is None:
        return Gate("employee_count", UNKNOWN, None, reason="site does not state headcount")
    if f["value"] > icp.employee_drop_above:
        return _fact_gate("employee_count", FAIL, f, reason=f"{f['value']} employees > {icp.employee_drop_above}")
    return _fact_gate("employee_count", PASS, f)


def audit_status(ev: dict, icp: ICP, today: date) -> Gate:
    """A fresh (<= audit_signal_max_age_days), dated signal passes. Undated or stale is unknown."""
    signals = list(ev.get("audit_signals", []))
    signals += [{"type": "certification_in_progress", "text": c["evidence_quote"], "url": c["source_url"],
                 "date": c.get("date")} for c in ev.get("certifications_held", []) if c["status"] == "in_progress"]
    valid, undated, stale = [], [], []
    for s in signals:
        if s["type"] in HIRE_TYPES and not keyword_hits(s["text"], icp.audit_role_keywords):
            continue           # the model called it a compliance/security hire; the keyword rule disagrees
        d = parse_date(s.get("date"))
        if d is None:
            undated.append(s)
        elif 0 <= (today - d).days <= icp.audit_signal_max_age_days:
            valid.append((d, s))
        else:
            stale.append(s)
    if valid:
        d, s = max(valid, key=lambda x: x[0])
        return Gate("audit_signal", PASS, {"type": s["type"], "count": len(valid)}, s["text"], s["url"],
                    s.get("date"), f"{len(valid)} signal(s) within {icp.audit_signal_max_age_days} days")
    if undated:
        s = undated[0]
        return Gate("audit_signal", UNKNOWN, {"type": s["type"], "undated": len(undated)}, s["text"], s["url"],
                    None, "signal found but undated; human picks the hook")
    if stale:
        s = stale[0]
        return Gate("audit_signal", UNKNOWN, {"type": s["type"], "stale": len(stale)}, s["text"], s["url"],
                    s.get("date"), f"only signals older than {icp.audit_signal_max_age_days} days")
    return Gate("audit_signal", UNKNOWN, None, reason="no audit signal found")


def evaluate_gates(ev: dict, company: dict, icp: ICP, today: date) -> list[Gate]:
    return [gate_saas(ev), gate_funding(company, icp), gate_certifications(ev, icp), gate_competitor(ev, icp),
            gate_employees(ev, icp), audit_status(ev, icp, today)]


# ---------------------------------------------------------------- decision + score

def decide(gates: list[Gate]) -> tuple[str, bool, str | None, list[str]]:
    by = {g.gate: g for g in gates}
    unknown = [g.gate for g in gates if g.result == UNKNOWN]
    failed = [g for g in gates if g.result == FAIL]
    if failed:
        return "dropped", False, "; ".join(f"{g.gate}: {g.reason}" for g in failed), unknown
    hard_unknown = [n for n in ("is_saas", "funding_stage") if by[n].result == UNKNOWN]
    if hard_unknown:
        return "flagged", True, f"unknown after research: {', '.join(hard_unknown)}", unknown
    if by["audit_signal"].result == UNKNOWN:
        return "qualified", True, f"audit signal unknown ({by['audit_signal'].reason})", unknown
    return "qualified", False, None, unknown


def fit_score(ev: dict, company: dict, icp: ICP) -> tuple[float, dict]:
    w = icp.fit_weights
    emp = (ev.get("employee_count") or {}).get("value")
    lo, hi = icp.employee_range
    held = {c["name"] for c in held_certs(ev, icp)}
    industry_text = " ".join(company.get("industry_tags") or []) + " " + ((ev.get("industry") or {}).get("value") or "")
    sensitive_aliases = [a for ind in icp.sensitive_industries for a in icp.industry_aliases.get(ind, [ind])]
    flags = {
        "employees_in_range": emp is not None and lo <= emp <= hi,
        "enterprise_customers": bool((ev.get("enterprise_customers") or {}).get("value"))
                                or any(s["type"] == "enterprise_customer" for s in ev.get("audit_signals", [])),
        "sensitive_industry": bool(keyword_hits(industry_text, sensitive_aliases)),
        "cloud_stack_known": ev.get("cloud_provider") is not None,
        "partial_certifications": 0 < len(held) < len(icp.certifications),
    }
    score = round(sum(w[k] for k, v in flags.items() if v), 3)
    return score, {"flags": flags, "weights": w, "held": sorted(held)}


# ---------------------------------------------------------------- agent loop

def chase_targets(gates: list[Gate]) -> list[str]:
    """Evidence fields worth another fetch, in priority order. Funding stage can't come from the website."""
    by = {g.gate: g for g in gates}
    return [field_ for gate, field_ in CHASE_ORDER if by[gate].result == UNKNOWN]


def page_available(ptype: str, scraper: Scraper, attempted: set[str]) -> bool:
    if ptype in attempted:
        return False
    if ptype == "careers" and scraper.ats_board and "ats" not in scraper.fetched_types():
        return True
    return ptype not in scraper.fetched_types() and ptype not in {p.page_type for p in scraper.pages}


def next_page_type(targets: list[str], icp: ICP, scraper: Scraper, attempted: set[str]) -> tuple[str, str] | None:
    for field_ in targets:
        for ptype in icp.refetch_map.get(field_, []):
            if page_available(ptype, scraper, attempted):
                return field_, ptype
    return None


RecordFn = Callable[[list[Page], int, str | None, dict | None], None]


def qualify_company(company: dict, scraper: Scraper, evidence: dict, record: RecordFn,
                    icp: ICP | None = None, today: date | None = None) -> tuple[dict, Outcome]:
    icp = icp or get_icp()
    today = today or date.today()
    attempted: set[str] = set()
    iterations: list[dict] = []
    gates = evaluate_gates(evidence, company, icp, today)
    for i in range(1, icp.max_refetches_per_company + 1):
        if any(g.result == FAIL for g in gates):
            break
        pick = next_page_type(chase_targets(gates), icp, scraper, attempted)
        if not pick or scraper.budget_left <= 0:
            break
        target, ptype = pick
        attempted.add(ptype)
        before = len(scraper.pages)
        page = scraper.fetch_type(ptype)
        new_pages = [p for p in scraper.pages[before:] if p.ok and p.text]
        new_ev = extract(company["name"], scraper.domain, new_pages) if new_pages else None
        record(scraper.pages[before:], i, target, new_ev)
        evidence = merge(evidence, new_ev)
        gates = evaluate_gates(evidence, company, icp, today)
        iterations.append({"iteration": i, "target": target, "page_type": ptype,
                           "url": page.url if page else None, "ok": bool(new_pages),
                           "gates": {g.gate: g.result for g in gates}})
        log.info("[%s] loop %d: chased %s via %s -> %s", scraper.domain, i, target, ptype,
                 "ok" if new_pages else "nothing")

    status, review, reason, unknown = decide(gates)
    out = Outcome(status, review, reason, unknown, gates, iterations=iterations)
    if status == "qualified":
        out.fit_score, out.fit_breakdown = fit_score(evidence, company, icp)
    return evidence, out


def gate_rows(out: Outcome) -> list[dict]:
    return [asdict(g) for g in out.gates]
