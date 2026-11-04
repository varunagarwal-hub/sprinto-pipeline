"""Qualifier rules: each spec gate, the audit-signal freshness rule, the final decision and the fit score."""
from datetime import date

from pipeline.config import load_icp
from pipeline.qualify import (FAIL, PASS, UNKNOWN, audit_status, chase_targets, decide, evaluate_gates,
                              fit_score, gate_certifications, gate_competitor, gate_employees, gate_funding,
                              gate_saas)

ICP = load_icp()
TODAY = date(2026, 11, 4)
U = "https://acme.io/"


def fact(value, quote="q", url=U, date_=None):
    return {"value": value, "evidence_quote": quote, "source_url": url, "date": date_}


def ev(**kw):
    base = {"is_saas": None, "product_summary": None, "certifications_held": [], "trust_center_vendor": None,
            "audit_signals": [], "employee_count": None, "enterprise_customers": None,
            "customer_count_claim": None, "industry": None, "cloud_provider": None,
            "_meta": {"pages": [{"url": U, "type": "homepage", "chars": 100}], "trust_vendor_html": []}}
    base.update(kw)
    return base


def with_pages(e, *types):
    e["_meta"]["pages"] += [{"url": f"{U}{t}", "type": t, "chars": 100} for t in types]
    return e


def cert(name, status="held"):
    return {"name": name, "status": status, "evidence_quote": f"{name} certified", "source_url": U, "date": None}


COMPANY = {"name": "Acme", "funding_stage": "Series B",
           "stage1_evidence": [{"gate": "funding_stage", "headline": "Acme raises $20M Series B",
                                "url": "https://news/1", "date": "2026-10-01"}]}


# --- individual gates

def test_saas_true_false_unknown():
    assert gate_saas(ev(is_saas=fact(True))).result == PASS
    assert gate_saas(ev(is_saas=fact(False))).result == FAIL
    assert gate_saas(ev()).result == UNKNOWN


def test_funding_stage_uses_stage1_evidence():
    g = gate_funding(COMPANY, ICP)
    assert g.result == PASS and g.source_url == "https://news/1"
    assert gate_funding({**COMPANY, "funding_stage": "Series D"}, ICP).result == FAIL
    assert gate_funding({**COMPANY, "funding_stage": None}, ICP).result == UNKNOWN


def test_all_three_certifications_drops():
    e = ev(certifications_held=[cert("SOC 2"), cert("ISO 27001"), cert("GDPR")])
    assert gate_certifications(e, ICP).result == FAIL


def test_partial_certifications_pass_and_in_progress_not_counted_as_held():
    e = ev(certifications_held=[cert("SOC 2"), cert("ISO 27001"), cert("GDPR", "in_progress")])
    g = gate_certifications(e, ICP)
    assert g.result == PASS and g.value == ["ISO 27001", "SOC 2"]


def test_certifications_unknown_until_trust_page_read():
    assert gate_certifications(ev(), ICP).result == UNKNOWN
    assert gate_certifications(with_pages(ev(), "trust"), ICP).result == PASS


def test_competitor_vendor_in_trust_page_html_drops():
    e = with_pages(ev(), "trust")
    e["_meta"]["trust_vendor_html"] = [{"vendor": "Vanta", "matched": "vanta.com", "url": f"{U}trust",
                                        "snippet": "assets.vanta.com/x.js"}]
    g = gate_competitor(e, ICP)
    assert g.result == FAIL and g.value == "Vanta"


def test_competitor_named_by_extractor_drops():
    e = ev(trust_center_vendor=fact("Drata"))
    assert gate_competitor(e, ICP).result == FAIL


def test_competitor_vendor_link_in_blog_footer_is_ignored():
    e = with_pages(ev(), "trust")
    e["_meta"]["trust_vendor_html"] = [{"vendor": "Vanta", "matched": "vanta.com", "url": U,
                                        "snippet": "read our post comparing vanta.com and others"}]
    assert gate_competitor(e, ICP).result == PASS


def test_existing_sprinto_customer_drops():
    e = with_pages(ev(), "trust")
    e["_meta"]["trust_vendor_html"] = [{"vendor": "Sprinto", "matched": "sprinto.com", "url": f"{U}trust",
                                        "snippet": "static.sprinto.com/_next/x.css"}]
    g = gate_competitor(e, ICP)
    assert g.result == FAIL and "existing customer" in g.reason


def test_non_competitor_vendor_passes():
    e = with_pages(ev(), "trust")
    e["_meta"]["trust_vendor_html"] = [{"vendor": "SafeBase", "matched": "safebase.io", "url": f"{U}trust",
                                        "snippet": "app.safebase.io"}]
    assert gate_competitor(e, ICP).result == PASS


def test_employee_drop_above_1000():
    assert gate_employees(ev(employee_count=fact(1500)), ICP).result == FAIL
    assert gate_employees(ev(employee_count=fact(120)), ICP).result == PASS
    assert gate_employees(ev(), ICP).result == UNKNOWN


# --- audit signal

def sig(text, date_, type_="compliance_hire"):
    return {"type": type_, "text": text, "url": U, "date": date_}


def test_fresh_dated_grc_role_passes():
    g = audit_status(ev(audit_signals=[sig("JOB: GRC Analyst", "2026-10-15")]), ICP, TODAY)
    assert g.result == PASS and g.evidence_date == "2026-10-15"


def test_stale_signal_is_unknown():
    g = audit_status(ev(audit_signals=[sig("JOB: GRC Analyst", "2026-05-01")]), ICP, TODAY)
    assert g.result == UNKNOWN and "older than" in g.reason


def test_undated_signal_is_unknown():
    g = audit_status(ev(audit_signals=[sig("We are pursuing SOC 2", None, "certification_in_progress")]), ICP, TODAY)
    assert g.result == UNKNOWN and "undated" in g.reason


def test_hire_without_audit_keyword_is_ignored():
    g = audit_status(ev(audit_signals=[sig("JOB: Senior Accountant", "2026-10-20")]), ICP, TODAY)
    assert g.result == UNKNOWN and g.reason == "no audit signal found"


def test_in_progress_certification_counts_as_signal():
    c = {**cert("SOC 2", "in_progress"), "date": "2026-10-01"}
    assert audit_status(ev(certifications_held=[c]), ICP, TODAY).result == PASS


# --- decision

def test_decide_all_pass_qualifies():
    e = with_pages(ev(is_saas=fact(True), audit_signals=[sig("JOB: GRC Analyst", "2026-10-15")]), "trust")
    status, review, _, _ = decide(evaluate_gates(e, COMPANY, ICP, TODAY))
    assert (status, review) == ("qualified", False)


def test_decide_audit_unknown_qualifies_with_review():
    e = with_pages(ev(is_saas=fact(True)), "trust")
    status, review, reason, _ = decide(evaluate_gates(e, COMPANY, ICP, TODAY))
    assert (status, review) == ("qualified", True) and "audit signal" in reason


def test_decide_saas_unknown_flags():
    status, review, reason, _ = decide(evaluate_gates(ev(), COMPANY, ICP, TODAY))
    assert (status, review) == ("flagged", True) and "is_saas" in reason


def test_decide_funding_unknown_flags():
    e = with_pages(ev(is_saas=fact(True)), "trust")
    status, _, reason, _ = decide(evaluate_gates(e, {**COMPANY, "funding_stage": None}, ICP, TODAY))
    assert status == "flagged" and "funding_stage" in reason


def test_decide_any_fail_drops_even_with_unknowns():
    e = ev(is_saas=fact(False))
    status, _, reason, _ = decide(evaluate_gates(e, COMPANY, ICP, TODAY))
    assert status == "dropped" and "is_saas" in reason


def test_chase_targets_priority_order():
    targets = chase_targets(evaluate_gates(ev(), COMPANY, ICP, TODAY))
    assert targets == ["is_saas", "trust_center_vendor", "certifications_held", "audit_signals", "employee_count"]


# --- fit score

def test_fit_score_components():
    e = ev(employee_count=fact(120), enterprise_customers=fact(["Nutanix"]), cloud_provider=fact("AWS"),
           certifications_held=[cert("SOC 2")], industry=fact("payments"))
    score, bd = fit_score(e, {"industry_tags": []}, ICP)
    assert all(bd["flags"].values())
    assert score == round(sum(ICP.fit_weights.values()), 3)


def test_fit_score_zero_when_nothing_known():
    score, bd = fit_score(ev(), {"industry_tags": []}, ICP)
    assert score == 0 and not any(bd["flags"].values())
