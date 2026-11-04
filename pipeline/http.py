"""One fetch() for every outbound HTTP GET: real User-Agent, timeout, retry with backoff,
on-disk cache in dev, and a logged (never raised) failure."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import requests

from pipeline.config import CACHE_DIR, get_icp, get_settings

log = logging.getLogger(__name__)

HTTP_CACHE = CACHE_DIR / "http"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int | None
    text: str
    ok: bool
    cached: bool = False
    error: str | None = None
    content_type: str = ""


def _cache_path(url: str):
    return HTTP_CACHE / f"{hashlib.sha256(url.encode()).hexdigest()}.json"


def fetch(url: str, *, use_cache: bool | None = None, timeout: float | None = None,
          headers: dict[str, str] | None = None, retries: int | None = None,
          quiet: bool = False) -> FetchResult:
    """GET url. use_cache defaults to True in dev. Retries network errors and 429/5xx only."""
    cfg = get_icp().http
    if use_cache is None:
        use_cache = get_settings().is_dev
    path = _cache_path(url)
    if use_cache and path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("fetched_at", None)
        return FetchResult(**{**data, "cached": True})

    hdrs = {"User-Agent": cfg["user_agent"], "Accept-Language": "en-US,en;q=0.9", **(headers or {})}
    retries = int(cfg["max_retries"]) if retries is None else retries
    result = FetchResult(url=url, final_url=url, status=None, text="", ok=False)
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=hdrs, timeout=timeout or cfg["timeout_seconds"], allow_redirects=True)
            if "charset" not in r.headers.get("content-type", "").lower():
                # requests falls back to ISO-8859-1 for text/* without a charset; sniff instead.
                r.encoding = r.apparent_encoding or "utf-8"
            result = FetchResult(url=url, final_url=r.url, status=r.status_code, text=r.text,
                                 ok=r.ok, content_type=r.headers.get("content-type", ""))
            if r.ok or r.status_code not in RETRYABLE_STATUS:
                break
            result.error = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            result = FetchResult(url=url, final_url=url, status=None, text="", ok=False,
                                 error=f"{type(e).__name__}: {e}")
        if attempt < retries:
            time.sleep(cfg["backoff_seconds"] * (2 ** attempt))

    if not result.ok:
        result.error = result.error or f"HTTP {result.status}"
        (log.debug if quiet else log.warning)("fetch failed %s: %s", url, result.error)
    elif use_cache:
        HTTP_CACHE.mkdir(parents=True, exist_ok=True)
        payload = {**asdict(result), "fetched_at": datetime.now(timezone.utc).isoformat()}
        payload.pop("cached")
        path.write_text(json.dumps(payload), encoding="utf-8")
    return result
