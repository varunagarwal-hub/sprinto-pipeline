"""Load secrets from .env and thresholds from icp.json into typed objects.

All tunable numbers live in icp.json; code reads them from here and never hard-codes them.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ICP_PATH = ROOT / "icp.json"
CACHE_DIR = ROOT / ".cache"

# Newest stable Flash model listed by models.list() on 2026-11-04; override with GEMINI_MODEL.
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    hunter_api_key: str
    database_url: str
    my_email: str
    env: str  # "dev" or "prod"
    hunter_mode: str  # "mock" (default) or "live": live Hunter calls spend real credits

    @property
    def is_dev(self) -> bool:
        return self.env != "prod"


@dataclass(frozen=True)
class ICP:
    funding_stages: list[str]
    funding_max_age_months: int
    news_queries: list[str]
    news_when: str
    news_regions: list[dict[str, str]]
    saas_keywords: list[str]
    exclude_keywords: list[str]
    industry_weights: dict[str, float]
    industry_aliases: dict[str, list[str]]
    rank_boost_keywords: list[str]
    rank_weights: dict[str, float]
    employee_range: list[int]
    employee_drop_above: int
    audit_signal_max_age_days: int
    audit_role_keywords: list[str]
    certifications: list[str]
    competitor_trust_vendors: list[str]
    poc_rules: dict[str, list[str]]
    poc_title_aliases: dict[str, list[str]]
    hunter_min_confidence: int
    hunter_monthly_searches: int
    hunter_search_params: dict[str, str]
    batch_size: int
    target_qualified_per_run: int
    max_batches_per_run: int
    max_refetches_per_company: int
    max_fetches_per_company: int
    seen_cooldown_days: int
    domain_tlds: list[str]
    domain_match_threshold: int
    page_keywords: dict[str, list[str]]
    initial_page_order: list[str]
    trust_vendor_domains: dict[str, list[str]]
    ats_max_jobs: int
    refetch_map: dict[str, list[str]]
    fit_weights: dict[str, float]
    sensitive_industries: list[str]
    http: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ICP":
        expected = {f.name for f in fields(cls)}
        missing = expected - data.keys()
        unknown = data.keys() - expected
        if missing or unknown:
            raise ValueError(
                f"icp.json mismatch. Missing: {sorted(missing) or '-'}; unknown: {sorted(unknown) or '-'}"
            )
        return cls(**data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL,
        hunter_api_key=os.getenv("HUNTER_API_KEY", ""),
        database_url=os.getenv("DATABASE_URL", ""),
        my_email=os.getenv("MY_EMAIL", ""),
        env=(os.getenv("PIPELINE_ENV") or "dev").lower(),
        hunter_mode=(os.getenv("HUNTER_MODE") or "mock").lower(),
    )


def load_icp(path: Path = ICP_PATH) -> ICP:
    with open(path, encoding="utf-8") as f:
        return ICP.from_dict(json.load(f))


@lru_cache(maxsize=1)
def get_icp() -> ICP:
    return load_icp()


def icp_raw() -> dict[str, Any]:
    """The icp.json contents as a plain dict, snapshotted onto each run for traceability."""
    with open(ICP_PATH, encoding="utf-8") as f:
        return json.load(f)
