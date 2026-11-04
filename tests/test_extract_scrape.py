"""Quote verification, ATS detection and HTML cleaning: the parts that keep the LLM honest."""
from pipeline.ats import find_ats, parse_jobs, render_jobs
from pipeline.config import load_icp
from pipeline.extract import merge, quote_found
from pipeline.scraper import Page, clean_text, detect_vendors

ICP = load_icp()
PAGES = [Page(url="https://acme.io/", page_type="homepage", status=200, ok=True,
              text="Acme is the AI platform for finance teams.\nWe’re SOC 2 Type II certified.")]


def test_quote_found_exact_and_curly_quotes():
    assert quote_found("We're SOC 2 Type II certified.", "https://acme.io/", PAGES)


def test_quote_not_found_when_invented():
    assert not quote_found("Acme is ISO 27001 certified.", "https://acme.io/", PAGES)
    assert not quote_found("", "https://acme.io/", PAGES)


def test_find_ats_variants():
    assert find_ats("https://boards.greenhouse.io/acme/jobs/123") == ("greenhouse", "acme")
    assert find_ats('<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme">') == ("greenhouse", "acme")
    assert find_ats("https://jobs.lever.co/findem") == ("lever", "findem")
    assert find_ats("https://jobs.ashbyhq.com/acme-inc") == ("ashby", "acme-inc")
    assert find_ats("https://acme.io/careers") is None


def test_render_jobs_puts_audit_roles_first_with_dates():
    body = '[{"text":"Senior Accountant","createdAt":1761100000000,"hostedUrl":"u1","categories":{}},' \
           '{"text":"GRC Analyst","createdAt":1761200000000,"hostedUrl":"u2","categories":{}}]'
    text = render_jobs(parse_jobs("lever", body), ICP.audit_role_keywords, 10)
    lines = text.splitlines()
    assert lines[1].startswith("JOB: GRC Analyst") and "posted: 2025-" in lines[1]


def test_clean_text_keeps_badges_and_logos_drops_scripts():
    html = ('<html><script>var x=1</script><nav>Menu</nav><main><p>Hello world</p>'
            '<img alt="SOC 2 Type II"><img alt="Nutanix logo"><img alt="Smiling person at desk"></main></html>')
    text = clean_text(html, "homepage", 5000)
    assert "Hello world" in text and "var x" not in text and "Menu" not in text
    assert "[image: SOC 2 Type II]" in text and "Nutanix" in text and "Smiling" not in text


def test_detect_vendor_in_raw_html():
    hits = detect_vendors('<script src="https://assets.vanta.com/x.js"></script>', "https://trust.acme.io/", ICP)
    assert hits[0]["vendor"] == "Vanta"


def test_merge_never_replaces_known_fact_with_null():
    known = {"evidence_quote": "q", "source_url": "u", "date": None, "value": True}
    base = {f: None for f in ["is_saas", "product_summary", "trust_center_vendor", "employee_count",
                              "enterprise_customers", "customer_count_claim", "industry", "cloud_provider"]}
    old = {**base, "is_saas": known, "certifications_held": [], "audit_signals": []}
    new = {**base, "certifications_held": [], "audit_signals": [{"type": "compliance_hire", "text": "JOB: GRC",
                                                                   "url": "u", "date": "2026-10-01"}]}
    out = merge(old, new)
    assert out["is_saas"] == known and len(out["audit_signals"]) == 1
