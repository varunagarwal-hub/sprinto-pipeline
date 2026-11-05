"""Feedback loop: the pure parts of Store (diffs, scores), Retrieve (context matching) and Apply
(hook ordering, rank adjustment, validator enforcement)."""
from datetime import date

from pipeline.config import ROOT, load_icp
from pipeline.feedback import (context_matches, hook_group, hook_scores_from, industry_adjust_from, ngrams,
                               phrase_diffs, role_key)
from pipeline.stage1 import NewsItem, rank
from pipeline.write import CitedText, EmailDraft, build_catalog, hook_candidates, role_angle, validate

ICP = load_icp()


def test_role_key_routes_titles():
    assert role_key("Chief Technology Officer") == "engineering"
    assert role_key("Compliance Manager") == "security"
    assert role_key("CEO") == "executive" and role_key("Co-Founder") == "executive"
    assert role_angle("CEO").startswith("founder/CEO")


def test_ngrams_need_two_content_words():
    grams = ngrams("we eliminate audit fire drills for you")
    assert "audit fire drills" in grams and "for you" not in " ".join(grams).split(",")


def test_phrase_diffs_find_what_reviewers_cut_and_add():
    pairs = [(1, "Sprinto ends audit fire drills fast.", "Sprinto keeps you audit ready."),
             (2, "No more audit fire drills here.", "Stay audit ready all year.")]
    removed, added = phrase_diffs(pairs)
    assert sorted(removed["audit fire drills"]) == [1, 2]
    assert "audit ready" not in removed


def test_hook_scores_are_smoothed_and_count_switches():
    s = hook_scores_from([("audit_signal", "replied"), ("audit_signal", "meeting"), ("funding", "no_reply"),
                          ("funding", "bounced")], [("funding", "audit_signal")])
    assert s["audit_signal"]["score"] > s["funding"]["score"]
    assert s["funding"]["support"] == 1.5          # bounce ignored, switch-away counts half


def test_industry_adjust_is_bounded():
    adj = industry_adjust_from([(["payments"], -1)] * 10 + [(["healthtech"], 1), (["healthtech"], 1)])
    assert -0.15 <= adj["payments"]["delta"] < 0 and adj["healthtech"]["delta"] > 0


def test_context_matching():
    assert context_matches({}, {"role": "security"})
    assert context_matches({"role": "security"}, {"role": "security", "industry": ["fintech"]})
    assert not context_matches({"role": "executive"}, {"role": "security"})
    assert context_matches({"industry": "fintech"}, {"industry": ["fintech", "payments"]})


def test_learned_hook_preference_reorders_candidates():
    company = {"funding_stage": "Series A", "funding_amount": "$5M",
               "stage1_evidence": [{"gate": "funding_stage", "headline": "Acme raises $5M Series A", "url": "u",
                                    "date": "2026-10-01"}]}
    ev = {"audit_signals": [], "certifications_held": [
        {"name": "SOC 2", "status": "held", "evidence_quote": "q", "source_url": "u", "date": "2026-09-01"}]}
    cat = build_catalog(company, ev)
    today = date(2026, 11, 4)
    assert hook_candidates(cat, ICP, today)[0]["field"] == "funding"
    assert hook_candidates(cat, ICP, today, {"certification": 0.8, "funding": 0.3})[0]["field"] == "certification"
    assert hook_group("audit_signal:compliance_hire") == "audit_signal"


def test_rank_applies_learned_industry_delta():
    item = NewsItem("Fintech Acme raises $5M Series A", "X", "u", date(2026, 10, 1), "q", "india")
    base, b0, _, _ = rank(item, 30, ICP)
    lowered, b1, _, _ = rank(item, 30, ICP, {"fintech": {"delta": -0.1, "id": 7}})
    assert round(base - lowered, 4) == 0.1 and b1["inputs"]["learning_ids"] == [7]


def test_validator_enforces_learned_avoid_phrases():
    company = {"name": "Acme", "domain": "acme.io", "funding_stage": "Series A", "funding_amount": None,
               "stage1_evidence": [{"gate": "funding_stage", "headline": "Acme raises Series A", "url": "u",
                                    "date": "2026-10-01"}]}
    cat = build_catalog(company, {"audit_signals": [], "certifications_held": []})
    body = "Hi Pat,\n\nAcme raises Series A news caught my eye.\n\nNo more audit fire drills.\n\nOpen to a 15-min call?\n\nThe Sprinto team"
    d = EmailDraft(pain_points=[CitedText(text="a", evidence_id="E1")] * 2, hook=CitedText(text="h", evidence_id="E1"),
                   subject="Acme Series A", body=body, claims=[])
    sprinto = (ROOT / "sprinto.md").read_text(encoding="utf-8")
    contact = {"first_name": "Pat", "full_name": "Pat X", "title": "CTO"}
    assert not any("reviewers keep removing" in e for e in validate(d, cat, "E1", company, contact, sprinto, "The Sprinto team"))
    errs = validate(d, cat, "E1", company, contact, sprinto, "The Sprinto team", ["audit fire drills"])
    assert any("reviewers keep removing" in e for e in errs)
