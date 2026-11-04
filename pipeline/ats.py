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


def render_jobs(jobs: list[Job], audit_keywords: list[str], max_jobs: int) -> str:
    """Plain-text job list for the extractor; audit-relevant roles first so truncation never hides them."""
    relevant = [j for j in jobs if keyword_hits(j.title, audit_keywords)]
    rest = [j for j in jobs if j not in relevant]
    lines = [f"Open roles: {len(jobs)} total."]
    for j in (relevant + rest)[:max_jobs]:
        lines.append(f"JOB: {j.title} | location: {j.location or 'n/a'} | posted: {j.posted or 'n/a'} | {j.url}")
    return "\n".join(lines)
