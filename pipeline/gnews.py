"""Decode a Google News RSS link to the publisher's article URL, and pull candidate company
websites out of that article. Used in Stage 2 domain resolution only (Stage 1 stays scrape-free)."""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup

from pipeline.config import CACHE_DIR, get_icp

log = logging.getLogger(__name__)
DECODE_CACHE = CACHE_DIR / "gnews"
_blocked = False   # set when Google answers 429 / "sorry"; never retried within the same process
_URL_RE = re.compile(r"https?://[^\"\\\s]+")
NOT_COMPANY_HOSTS = (
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com", "youtu.be",
    "crunchbase.com", "google.com", "apple.com", "medium.com", "wikipedia.org", "bloomberg.com", "reuters.com",
    "techcrunch.com", "tracxn.com", "pitchbook.com", "wa.me", "whatsapp.com", "t.me", "tiktok.com",
    "play.google.com", "apps.apple.com", "bit.ly", "lnkd.in", "substack.com", "github.com", "gstatic.com",
    "googleapis.com", "cloudflare.com", "wordpress.com", "gravatar.com", "doubleclick.net", "amazon.com",
    "prnewswire.com", "businesswire.com", "globenewswire.com", "yahoo.com", "vccircle.com", "inc42.com",
)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def decode(news_url: str) -> str | None:
    """news.google.com/rss/articles/<id> -> publisher URL (two small requests, cached on disk)."""
    if "news.google.com" not in (news_url or ""):
        return news_url or None
    aid = news_url.split("/articles/")[-1].split("?")[0]
    global _blocked
    cache = DECODE_CACHE / f"{aid[:120]}.txt"
    if cache.exists():
        return cache.read_text(encoding="utf-8") or None
    if _blocked:
        return None
    ua = {"User-Agent": get_icp().http["user_agent"]}
    try:
        r = requests.get(f"https://news.google.com/articles/{aid}", headers=ua, timeout=12)
        if r.status_code == 429 or "/sorry/" in r.url:
            # Google is rate-limiting us. Respect it: stop decoding for the rest of this process.
            _blocked = True
            log.warning("Google News rate-limited this client; skipping article lookups for this run")
            return None
        page = r.text
        sg = re.search(r'data-n-a-sg="([^"]+)"', page)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page)
        if not (sg and ts):
            return None
        inner = ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None,
                                 None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
                 aid, int(ts.group(1)), sg.group(1)]
        resp = requests.post("https://news.google.com/_/DotsSplashUi/data/batchexecute", timeout=12,
                             headers={**ua, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                             data="f.req=" + quote(json.dumps([[["Fbv4je", json.dumps(inner)]]])))
        i = resp.text.find("garturlres")
        m = _URL_RE.search(resp.text[i:]) if i >= 0 else None
        url = m.group(0) if m else None
    except requests.RequestException as e:
        log.debug("gnews decode failed: %s", type(e).__name__)
        return None
    if url:                                    # cache successes only
        DECODE_CACHE.mkdir(parents=True, exist_ok=True)
        cache.write_text(url, encoding="utf-8")
    return url


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def company_links(article_html: str, article_url: str, company: str) -> list[str]:
    """Outbound hosts in the article that look like the company's own site, best first."""
    pub = _host(article_url)
    name = _compact(company)
    soup = BeautifulSoup(article_html, "html.parser")
    scored: dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        host = _host(href)
        if not host or host == pub or host.endswith("." + pub) or host.endswith(NOT_COMPANY_HOSTS):
            continue
        label = _compact(host.split(".")[0])
        text = _compact(a.get_text(" ", strip=True))
        score = 0
        if name and (name in _compact(host) or (len(label) >= 3 and label in name)):
            score += 2                      # the host itself carries the company name
        if name and name in text:
            score += 1                      # anchor text names the company
        if score:
            scored[host] = max(scored.get(host, 0), score)
    return [h for h, _ in sorted(scored.items(), key=lambda x: -x[1])]
