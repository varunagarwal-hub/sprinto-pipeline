"""Gemini extractor: page text -> strict JSON where every fact carries a verbatim quote + source URL.

After the model answers, Python checks each evidence_quote really occurs in the cited page.
Facts whose quote can't be found are nulled and listed under _meta.rejected_facts.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel
from rapidfuzz import fuzz

from pipeline.llm import generate_json
from pipeline.scraper import Page

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- response schema

class Fact(BaseModel):
    evidence_quote: str
    source_url: str
    date: Optional[str]


class BoolFact(Fact):
    value: bool


class TextFact(Fact):
    value: str


class IntFact(Fact):
    value: int


class NamesFact(Fact):
    value: list[str]


class CertItem(BaseModel):
    name: Literal["SOC 2", "ISO 27001", "GDPR", "HIPAA", "PCI DSS", "Other"]
    status: Literal["held", "in_progress", "mentioned"]
    evidence_quote: str
    source_url: str
    date: Optional[str]


class AuditSignal(BaseModel):
    type: Literal["compliance_hire", "security_hire", "certification_in_progress", "enterprise_customer",
                  "regulated_expansion", "security_questionnaire", "other"]
    text: str          # verbatim quote
    url: str
    date: Optional[str]


class Extraction(BaseModel):
    is_saas: Optional[BoolFact]
    product_summary: Optional[TextFact]
    certifications_held: list[CertItem]
    trust_center_vendor: Optional[TextFact]
    audit_signals: list[AuditSignal]
    employee_count: Optional[IntFact]
    enterprise_customers: Optional[NamesFact]
    customer_count_claim: Optional[TextFact]
    industry: Optional[TextFact]
    cloud_provider: Optional[TextFact]


SCALAR_FIELDS = ["is_saas", "product_summary", "trust_center_vendor", "employee_count", "enterprise_customers",
                 "customer_count_claim", "industry", "cloud_provider"]

SYSTEM = """You extract facts about ONE company from text scraped from its own website, for a compliance-software sales team.

Hard rules:
1. Use only the text provided. Never infer, guess, or use outside knowledge. If a fact is not explicitly stated, return null (or an empty list). Null is always acceptable and is better than a guess.
2. evidence_quote (and audit_signals.text) must be copied verbatim from the page text: one contiguous span, at most 300 characters. Do not paraphrase, fix typos, or join separate lines.
3. source_url (and audit_signals.url) must be exactly the url of the PAGE block the quote comes from.
4. date: only when a date is stated alongside that fact (a job's posted date, a dated announcement). Format YYYY-MM-DD or YYYY-MM. Otherwise null.

Field guidance:
- is_saas: true only if the text describes a software product customers use online or in the cloud (platform, app, API, subscription). false only if the text clearly says the main business is services, consulting, agency work, hardware or physical goods. Otherwise null.
- product_summary: one sentence quoted from the text that best says what the product does.
- certifications_held: each compliance framework the company says it holds or complies with (status "held"), is working towards (status "in_progress"), or only mentions (status "mentioned"). "SOC 2 Type II certified" or a badge line like "[image: SOC 2 Type II]" means held.
- trust_center_vendor: only if the text names the vendor that runs their trust center (e.g. "Powered by Vanta").
- audit_signals: evidence the company will soon need a compliance audit: open roles in GRC, compliance, security, privacy or risk; statements about pursuing a certification; new enterprise or regulated-industry customers; expansion into regulated markets; security reviews or questionnaires. For a JOB line, quote the whole JOB line as text, use the page url, and use its posted date.
- employee_count: only an explicit number of employees or team size.
- enterprise_customers: customer names listed on the page that are large enterprises or well-known brands.
- customer_count_claim: a claim like "Trusted by 500+ companies".
- industry: the industry the product serves, in a few words, quoted.
- cloud_provider: only if a cloud provider (AWS, Google Cloud, Azure...) is named as their hosting or infrastructure."""


def build_prompt(company: str, domain: str, pages: list[Page]) -> str:
    blocks = [f"Company: {company} ({domain})"]
    for p in pages:
        blocks.append(f"=== PAGE url={p.url} type={p.page_type} ===\n{p.text}")
    return "\n\n".join(blocks)


# ---------------------------------------------------------------- quote verification

_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.translate(_QUOTES)).strip().lower()


def quote_found(quote: str, url: str, pages: list[Page], threshold: int = 92) -> bool:
    if not quote or not quote.strip():
        return False
    q = _norm(quote)
    texts = [_norm(p.text) for p in pages if p.url == url] or [_norm(p.text) for p in pages]
    if any(q in t for t in texts):
        return True
    return any(fuzz.partial_ratio(q, t) >= threshold for t in texts)


def verify(ex: Extraction, pages: list[Page]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = ex.model_dump()
    rejected: list[dict[str, Any]] = []
    for f in SCALAR_FIELDS:
        fact = data.get(f)
        if fact and not quote_found(fact["evidence_quote"], fact["source_url"], pages):
            rejected.append({"field": f, **fact})
            data[f] = None
    keep = []
    for c in data["certifications_held"]:
        if quote_found(c["evidence_quote"], c["source_url"], pages):
            keep.append(c)
        else:
            rejected.append({"field": "certifications_held", **c})
    data["certifications_held"] = keep
    keep = []
    for s in data["audit_signals"]:
        if quote_found(s["text"], s["url"], pages):
            keep.append(s)
        else:
            rejected.append({"field": "audit_signals", **s})
    data["audit_signals"] = keep
    return data, rejected


# ---------------------------------------------------------------- public API

def extract(company: str, domain: str, pages: list[Page]) -> dict[str, Any] | None:
    """Run the extractor over pages. Returns the verified evidence dict, or None if Gemini failed."""
    usable = [p for p in pages if p.ok and p.text]
    if not usable:
        return None
    res = generate_json(build_prompt(company, domain, usable), Extraction, system=SYSTEM, stage="extract")
    if res.data is None:
        return None
    data, rejected = verify(res.data, usable)
    data["_meta"] = {
        "pages": [{"url": p.url, "type": p.page_type, "chars": len(p.text)} for p in usable],
        "trust_vendor_html": [h for p in usable for h in p.vendor_hits],
        "rejected_facts": rejected,
        "tokens_in": res.tokens_in,
        "tokens_out": res.tokens_out,
        "cached": res.cached,
    }
    if rejected:
        log.info("[%s] %d fact(s) rejected: quote not found on page", domain, len(rejected))
    return data


def merge(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any] | None:
    """Combine extractions across agent-loop iterations: a known fact is never replaced by null."""
    if not old:
        return new
    if not new:
        return old
    out = dict(old)
    for f in SCALAR_FIELDS:
        if out.get(f) is None and new.get(f) is not None:
            out[f] = new[f]
    seen_c = {(c["name"], c["status"]) for c in out["certifications_held"]}
    out["certifications_held"] = out["certifications_held"] + [
        c for c in new["certifications_held"] if (c["name"], c["status"]) not in seen_c]
    seen_s = {_norm(s["text"]) for s in out["audit_signals"]}
    out["audit_signals"] = out["audit_signals"] + [s for s in new["audit_signals"] if _norm(s["text"]) not in seen_s]
    om, nm = out.get("_meta", {}), new.get("_meta", {})
    out["_meta"] = {
        "pages": om.get("pages", []) + nm.get("pages", []),
        "trust_vendor_html": om.get("trust_vendor_html", []) + nm.get("trust_vendor_html", []),
        "rejected_facts": om.get("rejected_facts", []) + nm.get("rejected_facts", []),
        "tokens_in": om.get("tokens_in", 0) + nm.get("tokens_in", 0),
        "tokens_out": om.get("tokens_out", 0) + nm.get("tokens_out", 0),
    }
    return out
