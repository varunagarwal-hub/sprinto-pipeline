"""Email validator + hook selection: the checks that stop invented facts reaching an inbox."""
from datetime import date

from pipeline.config import ROOT, load_icp
from pipeline.mailer import edit_rate, subject_line
from pipeline.write import CitedText, EmailDraft, build_catalog, hook_candidates, role_angle, validate

ICP = load_icp()
SPRINTO = (ROOT / "sprinto.md").read_text(encoding="utf-8")
COMPANY = {
    "name": "Pearler", "domain": "pearler.com", "funding_stage": "Series A", "funding_amount": "$16 million",
    "stage1_evidence": [{"gate": "funding_stage", "headline": "Investment fintech Pearler bags $16 million Series A",
                         "url": "https://news/1", "date": "2026-09-03"}],
}
EVIDENCE = {
    "audit_signals": [{"type": "compliance_hire", "text": "JOB: GRC Analyst | posted: 2026-10-20", "url": "u",
                       "date": "2026-10-20"}],
    "certifications_held": [{"name": "ISO 27001", "status": "held", "evidence_quote": "Pearler is ISO 27001 certified.",
                             "source_url": "https://pearler.com/legal/security", "date": None}],
    "enterprise_customers": None, "product_summary": None, "customer_count_claim": None, "industry": None,
    "cloud_provider": None, "employee_count": None,
}
CONTACT = {"first_name": "Arjun", "full_name": "Arjun Mock-Rao", "title": "Chief Technology Officer"}
CATALOG = build_catalog(COMPANY, EVIDENCE)
TODAY = date(2026, 11, 4)

GOOD_BODY = """Hi Arjun,

Saw Pearler posted a GRC Analyst role on 20 Oct, right after the $16 million Series A.

Pearler is already ISO 27001 certified. Adding SOC 2 usually means another round of evidence gathering for engineering. Sprinto maps controls across frameworks, so most of that ISO work carries over, and evidence collection runs automatically.

Worth a 15-min call next week to see if it fits?

The Sprinto team"""


def draft(body=GOOD_BODY, hook="E2", pains=("E2", "E3"), claims=("E1", "E2", "E3"), subject="SOC 2 on top of ISO 27001"):
    return EmailDraft(pain_points=[CitedText(text="p", evidence_id=e) for e in pains],
                      hook=CitedText(text="GRC hire", evidence_id=hook), subject=subject, body=body,
                      claims=[CitedText(text="c", evidence_id=e) for e in claims])


def check(d, hook_id="E2"):
    return validate(d, CATALOG, hook_id, COMPANY, CONTACT, SPRINTO, "The Sprinto team")


def test_catalog_ids_and_fields():
    assert [it["field"] for it in CATALOG] == ["funding", "audit_signal:compliance_hire", "certification"]
    assert CATALOG[0]["fact"] == "Raised $16 million Series A"


def test_hook_prefers_fresh_audit_signal_then_funding():
    cands = hook_candidates(CATALOG, ICP, TODAY)
    assert [c["id"] for c in cands] == ["E2", "E1"]          # undated certification is never a hook


def test_good_draft_passes():
    assert check(draft()) == []


def test_invented_number_and_customer_fail():
    body = GOOD_BODY.replace("right after the $16 million Series A", "right after signing Goldman Sachs and 400 banks")
    errs = check(draft(body))
    assert any("400" in e for e in errs) and any("Goldman" in e for e in errs)


def test_unknown_evidence_id_fails():
    errs = check(draft(pains=("E2", "E9")))
    assert any("E9" in e for e in errs)


def test_wrong_hook_fails():
    assert any("hook must cite E2" in e for e in check(draft(hook="E1")))


def test_needs_exactly_two_pain_points():
    assert any("exactly 2" in e for e in check(draft(pains=("E2",))))


def test_greeting_word_count_ask_and_plain_text():
    assert any("first name" in e for e in check(draft(GOOD_BODY.replace("Hi Arjun,", "Hello there,"))))
    assert any("words" in e for e in check(draft(GOOD_BODY + " filler" * 80)))
    assert any("question mark" in e for e in check(draft(GOOD_BODY.replace("work carries over,", "work carries over? Right?"))))
    assert any("15-minute" in e for e in check(draft(GOOD_BODY.replace("15-min call", "chat"))))
    assert any("plain text" in e for e in check(draft(GOOD_BODY.replace("Saw Pearler", "**Saw** Pearler"))))


def test_opening_must_reference_hook():
    body = GOOD_BODY.replace("Saw Pearler posted a GRC Analyst role on 20 Oct, right after the $16 million Series A.",
                             "Hope your week is going well.")
    assert any("opening lines" in e for e in check(draft(body)))


def test_role_angle():
    assert role_angle("Chief Technology Officer").startswith("engineering")
    assert role_angle("Head of Security & Compliance").startswith("security")


def test_edit_rate_and_subject_prefix():
    assert edit_rate("abc", "abc") == 0 and 0 < edit_rate("hello world", "hello there") < 1
    assert subject_line("Arjun Mock-Rao", "Pearler", "Hi") == "[DRAFT for Arjun Mock-Rao @ Pearler] Hi"
