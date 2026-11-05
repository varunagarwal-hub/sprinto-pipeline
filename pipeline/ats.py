"""Greenhouse / Lever / Ashby public job-board APIs.

Careers pages are often JS shells; the boards' public JSON gives titles *and posting dates*,
which the 90-day audit-signal rule and the dated email hook both need.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from pipeline.stage1 import keyword_hits

_ATS_RES = [
    ("greenhouse", re.compile(r"greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
]
_BAD_TOKENS = {"embed", "v1", "js", "jobs", "job_board", "api"}
ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com")


@dataclass
class Job:
    title: str
    location: str
    posted: str | None   # ISO date
    url: str


def find_ats(text: str) -> tuple[str, str] | None:
    """Find the first ATS board referenced in a URL or raw HTML. Returns (kind, board token)."""
    for kind, rx in _ATS_RES:
        for m in rx.finditer(text):
            token = m.group(1).strip(".")
            if token.lower() not in _BAD_TOKENS:
                return kind, token
    return None


def api_url(kind: str, token: str) -> str:
    return {
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
        "lever": f"https://api.lever.co/v0/postings/{token}?mode=json",
        "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{token}",
    }[kind]


def _iso(value) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):  # Lever: epoch milliseconds
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def parse_jobs(kind: str, body: str) -> list[Job]:
    data = json.loads(body)
    jobs: list[Job] = []
    if kind == "greenhouse":
        for j in data.get("jobs", []):
            jobs.append(Job(j.get("title", ""), (j.get("location") or {}).get("name", ""),
                            _iso(j.get("first_published") or j.get("updated_at")), j.get("absolute_url", "")))
    elif kind == "lever":
        for j in data if isinstance(data, list) else []:
            jobs.append(Job(j.get("text", ""), (j.get("categories") or {}).get("location", "") or "",
                            _iso(j.get("createdAt")), j.get("hostedUrl", "")))
    elif kind == "ashby":
        for j in data.get("jobs", []):
            jobs.append(Job(j.get("title", ""), j.get("location", "") or "",
                            _iso(j.get("publishedAt")), j.get("jobUrl", "")))
    return jobs


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def probe_slugs(domain: str, company: str) -> list[str]:
    label = domain.split(".")[0]
    return list(dict.fromkeys(s for s in [label, _compact(company)] if len(s) >= 3))


def probe(domain: str, company: str, fetch_fn) -> tuple[str, str, str, list[Job]] | None:
    """Try public Greenhouse / Lever / Ashby boards named after the domain/company.
    Accept only if the board's raw JSON mentions the company name (guards against same-slug strangers).
    Returns (kind, token, api_url, jobs). fetch_fn(url) -> FetchResult."""
    name = _compact(company)
    for slug in probe_slugs(domain, company):
        for kind in ("greenhouse", "lever", "ashby"):
            url = api_url(kind, slug)
            r = fetch_fn(url)
            if not r.ok or not r.text.strip():
                continue
            try:
                jobs = parse_jobs(kind, r.text)
            except ValueError:
                continue
            if not jobs:
                continue
            if kind == "greenhouse":
                meta = fetch_fn(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
                ok = meta.ok and name in _compact(meta.text)
            else:
                ok = name in _compact(r.text) or _compact(domain) in _compact(r.text)
            if ok:
                return kind, slug, url, jobs
    return None


def render_jobs(jobs: list[Job], audit_keywords: list[str], max_jobs: int) -> str:
    """Plain-text job list for the extractor; audit-relevant roles first so truncation never hides them."""
    relevant = [j for j in jobs if keyword_hits(j.title, audit_keywords)]
    rest = [j for j in jobs if j not in relevant]
    lines = [f"Open roles: {len(jobs)} total."]
    for j in (relevant + rest)[:max_jobs]:
        lines.append(f"JOB: {j.title} | location: {j.location or 'n/a'} | posted: {j.posted or 'n/a'} | {j.url}")
    return "\n".join(lines)
