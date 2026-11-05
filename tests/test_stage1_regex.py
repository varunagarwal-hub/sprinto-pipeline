"""Stage 1 parsing and gate rules. Headlines are real Google News titles seen during development."""
from datetime import date

import pytest

from pipeline.config import load_icp
from pipeline.stage1 import (NewsItem, detect_amount, detect_stage, evaluate_item, keyword_hits,
                             norm_name, parse_company_name, strip_publisher)

ICP = load_icp()
TODAY = date(2026, 11, 4)


@pytest.mark.parametrize("headline, expected", [
    ("Optio raises $1M Series A to launch SaaS and expand abroad", "Optio"),
    ("Logistics startup OORJAA raises Series A funding led by Equentis Angel Fund", "OORJAA"),
    ("Rillet raises $100M Series C at $1B valuation", "Rillet"),
    ("SaaS startup Growfin raises $7.5M in Series A funding led by SWC Global", "Growfin"),
    ("Accel-backed Venwiz raises $8.3 mn in Series A round", "Venwiz"),
    ("SaaS Logistics Startup Shipsy Raises $6 Mn In Series A From Sequoia", "Shipsy"),
    ("HR tech startup Keka raises India's largest series A SaaS funding of $57 million", "Keka"),
    ("E-commerce SaaS start-up Graas raises $40 million in first close of Series A", "Graas"),
    ("SaaS startup Drivetrain AI raises $15M in Series A round", "Drivetrain AI"),
    ("Dispute automation platform Casap bags $56m Series B", "Casap"),
    ("US fintech Meow lands $31.6m Series B funding", "Meow"),
    ("Argentinian fintech Tapi secures $27m in Series B funding round", "Tapi"),
    ("Wint Wealth raises INR 250 Cr in Series B funding", "Wint Wealth"),
    ("Exclusive: Acme Cloud raises $12M Series A", "Acme Cloud"),
    ("Acme, a Bengaluru-based fintech startup, raises $5M Series A", "Acme"),
    ("Mexican Plenna Raises $6M Series A to Expand", "Plenna"),
    ("Norway's Unleash secures $35M Series B", "Unleash"),
    ("Latin America’s Mendel secures $35m Series B", "Mendel"),
    ("Tentree spinout Veritree nabs $9.1-million Series A", "Veritree"),
    ("Credilio’s credit card biz Novio raises Rs 100 Cr in Series A", "Novio"),
    ("Human risk platform usecure secures Series A from Kennet", "usecure"),
    ("groundcover Raises $35Million Series B", "groundcover"),
    ("AI software start-up Jump pockets $80m Series B", "Jump"),
    ("Barq hits unicorn status with $329.5m Series A", "Barq"),
    ("Payments infrastructure start-up Fun exits stealth with $72m Series A", "Fun"),
    ("Egyptian Fintech Lucky Hits Profitability, Raises $23m Series B", "Lucky"),
    ("US wealthtech Bunch raises $10M Series B", "Bunch"),
])
def test_parse_company_name(headline, expected):
    assert parse_company_name(headline) == expected


@pytest.mark.parametrize("headline", [
    "Fintech startups Kiwi, CredRight raise Series B funding",      # two companies, plural verb
    "Osborne Clarke advises Triver on €8m Series B funding round",   # advisor, not the company
    "Fintech Simetrik: US$55m Series B Funding Boost",               # no funding verb
    "How startups raise a Series A in 2026",                         # no company
    "Startup raises $10M Series A",                                  # descriptor only
    "Cincinnati fintech closes $9M Series A led by Fifth Third Bank", # description, not a name
])
def test_parse_company_name_rejects(headline):
    assert parse_company_name(headline) is None


@pytest.mark.parametrize("text, expected", [
    ("Acme raises $10M Series A", "Series A"),
    ("Acme bags series-b funding", "Series B"),
    ("Acme closes Series C round", "Series C"),
    ("Acme raises Series D", "Series D"),
    ("Acme raises pre-Series A round", None),
    ("Acme raises seed round", None),
])
def test_detect_stage(text, expected):
    assert detect_stage(text) == expected


def test_detect_amount():
    assert detect_amount("Optio raises $1M Series A") == "$1M"
    assert detect_amount("OORJAA raises ₹9.7 Cr in series A") == "₹9.7 Cr"
    assert detect_amount("Wint Wealth raises INR 250 Cr in Series B") == "INR 250 Cr"
    assert detect_amount("Acme raises Series A") is None


def test_norm_name_strips_suffixes_and_punctuation():
    assert norm_name("Drivetrain AI") == "drivetrain"
    assert norm_name("Acme Technologies Pvt. Ltd.") == "acme"
    assert norm_name("AI") == "ai"                     # never strip to empty
    assert norm_name("Wint Wealth") == "wint wealth"


def test_strip_publisher():
    assert strip_publisher("Optio raises $1M Series A - Dealroom", "Dealroom") == "Optio raises $1M Series A"
    assert strip_publisher("A - B raises Series A - TechCrunch") == "A - B raises Series A"


def test_keyword_hits_respects_word_boundaries():
    assert keyword_hits("Acme API platform", ["api"]) == ["api"]
    assert keyword_hits("Acme rapid growth", ["api"]) == []


def _item(headline: str, published: date = date(2026, 10, 1)) -> NewsItem:
    return NewsItem(headline=headline, publisher="X", url="https://n/1", published=published,
                    query="q", region="india")


def test_gate_drops_stage_out_of_range():
    cand, reason = evaluate_item(_item("Acme raises $90M Series D"), ICP, TODAY)
    assert cand is None and reason == "stage_out_of_range:Series D"


def test_gate_drops_seed_round_mentioning_future_series():
    cand, reason = evaluate_item(
        _item("Prowrrap Raises ₹4 Crore Seed Round; Sets Target for Series A in 18–24 Months"), ICP, TODAY)
    assert cand is None and reason == "seed_round"


def test_gate_drops_old_article():
    cand, reason = evaluate_item(_item("Acme raises $9M Series A", date(2023, 3, 1)), ICP, TODAY)
    assert cand is None and reason == "article_too_old"


def test_gate_drops_excluded_keyword():
    cand, reason = evaluate_item(_item("Drone maker Skyfly raises $9M Series A"), ICP, TODAY)
    assert cand is None and reason.startswith("excluded_keyword")


def test_gate_keeps_without_saas_keyword_and_marks_unknown():
    cand, reason = evaluate_item(_item("Acme raises $9M Series A"), ICP, TODAY)
    assert reason is None and cand.stage == "Series A"
    saas_gate = next(g for g in cand.gates if g["gate"] == "saas_keyword")
    assert saas_gate["result"] == "unknown"


def test_rank_prefers_recent_fintech_over_old_generic():
    recent_fintech, _ = evaluate_item(_item("Fintech startup Payco raises $9M Series A", date(2026, 10, 20)), ICP, TODAY)
    old_generic, _ = evaluate_item(_item("Acme raises $9M Series A", date(2025, 6, 1)), ICP, TODAY)
    assert recent_fintech.rank_score > old_generic.rank_score
