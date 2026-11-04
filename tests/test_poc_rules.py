"""PoC picking: stage rules, title aliases, verification/confidence preference, and exclusions."""
import json

from pipeline.config import load_icp
from pipeline.poc import MOCK_PATH, pick_contact, title_rule

ICP = load_icp()
MOCK = json.loads(MOCK_PATH.read_text(encoding="utf-8").replace("{domain}", "acme.io").replace("{org}", "Acme"))
EMAILS = MOCK["data"]["emails"]


def person(position, confidence=90, status="valid", first="Pat", value=None):
    return {"value": value or f"{first.lower()}@acme.io", "type": "personal", "confidence": confidence,
            "first_name": first, "last_name": "X", "position": position,
            "verification": {"status": status}, "sources": []}


def test_title_aliases_and_word_boundaries():
    rules = ICP.poc_rules["Series A"]
    assert title_rule("Chief Technology Officer", rules, ICP) == (0, "CTO")
    assert title_rule("VP of Engineering", rules, ICP) == (1, "VP Engineering")
    assert title_rule("Co-Founder & CEO", rules, ICP) == (2, "Co-founder")
    assert title_rule("Director of Marketing", rules, ICP) is None     # 'cto' inside 'Director' must not match


def test_series_a_prefers_cto_with_verified_high_confidence_email():
    pick, considered = pick_contact(EMAILS, "Series A", ICP)
    assert pick.rule == "CTO" and pick.email["value"] == "arjun@acme.io"   # not the 41%-confidence duplicate
    skips = {c["email"]: c.get("skip") for c in considered}
    assert skips["info@acme.io"] == "generic mailbox"
    assert skips["arjun.rao@acme.io"].startswith("confidence 41")
    assert skips["maya@acme.io"].startswith("title not in poc_rules")


def test_series_c_prefers_security_leader_over_cto():
    pick, _ = pick_contact(EMAILS, "Series C", ICP)
    assert pick.rule == "Head of Security" and pick.email["first_name"] == "Sam"


def test_series_c_falls_back_to_cto():
    emails = [person("CTO", first="Ana"), person("Head of Sales", first="Bo")]
    pick, _ = pick_contact(emails, "Series C", ICP)
    assert pick.rule == "CTO" and pick.email["first_name"] == "Ana"


def test_same_rule_prefers_verified_then_confidence():
    emails = [person("CTO", 95, "accept_all", "Accept"), person("CTO", 80, "valid", "Valid")]
    pick, _ = pick_contact(emails, "Series B", ICP)
    assert pick.email["first_name"] == "Valid"


def test_invalid_email_never_picked():
    pick, considered = pick_contact([person("CTO", 99, "invalid")], "Series A", ICP)
    assert pick is None and considered[0]["skip"] == "verification invalid"


def test_no_match_returns_none():
    pick, _ = pick_contact([person("Head of Sales")], "Series B", ICP)
    assert pick is None


def test_unknown_stage_matches_nothing():
    pick, _ = pick_contact(EMAILS, None, ICP)
    assert pick is None
