"""Company name -> domain. Try {slug}{tld} candidates and accept one only if the homepage's
brand text names the company AND the homepage shares a topic word with the funding headline.
Anything weaker is unresolved: the company is flagged, never guessed."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from pipeline.config import ICP, get_icp
from pipeline.http import fetch

log = logging.getLogger(__name__)

PARKED = re.compile(r"domain (?:is )?for sale|buy this domain|parked|hugedomains|sedo\.com|dan\.com|"
                    r"godaddy|this domain may be for sale|domain name is available", re.I)
GENERIC_WORDS = {
    "raises", "raised", "secures", "series", "funding", "round", "million", "billion", "startup", "platform",
    "company", "expand", "expansion", "scale", "accelerate", "growth", "global", "launch", "valuation",
    "investors", "capital", "ventures", "partners", "led", "new", "first", "close", "extension", "with",
    "from", "into", "bags", "lands", "closes", "backed", "india", "indian", "help", "helps", "build",
    "more", "their", "across", "after", "years", "markets", "market",
}


@dataclass
class Resolution:
    domain: str | None
    tried: list[dict] = field(default_factory=list)
    evidence: dict | None = None


def slugs(name: str) -> list[str]:
    low = name.lower().strip()
    if re.fullmatch(r"[a-z0-9-]+\.[a-z]{2,6}", low):          # name is already a domain: "CARPL.ai"
        return [low]
    words = re.sub(r"[^a-z0-9 ]+", " ", low).split()
    out = ["".join(words)]
    if len(words) > 1:
        out.append("-".join(words))
    return [s for s in dict.fromkeys(out) if s]


def candidates(name: str, icp: ICP) -> list[str]:
    out = []
    for s in slugs(name):
        if "." in s:
            out.append(s)
        else:
            out += [f"{s}{tld}" for tld in icp.domain_tlds]
    return out


def brand_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    parts = [soup.title.get_text(" ", strip=True) if soup.title else ""]
    for prop in ("og:site_name", "og:title", "application-name"):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            parts.append(tag["content"])
    for img in soup.find_all("img", alt=True)[:15]:
        if "logo" in img["alt"].lower() or "logo" in " ".join(img.get("class", [])).lower():
            parts.append(img["alt"])
    return " | ".join(p for p in parts if p)


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def name_matches(name: str, brand: str) -> bool:
    """Whole-word match of the name in the brand text (spaces/punctuation ignored)."""
    n = re.sub(r"[^a-z0-9 ]+", " ", name.lower()).split()
    b = re.sub(r"[^a-z0-9 ]+", " ", brand.lower())
    if re.search(r"\b" + r"\s*".join(map(re.escape, n)) + r"\b", b):
        return True
    return len(_compact(name)) >= 6 and _compact(name) in _compact(brand)


def topic_words(headline: str, name: str) -> set[str]:
    name_words = set(re.sub(r"[^a-z0-9 ]+", " ", name.lower()).split())
    words = re.findall(r"[a-z][a-z-]{3,}", headline.lower())
    return {w[:5] for w in words if w not in GENERIC_WORDS and w not in name_words}


def topic_overlap(headline: str, name: str, page_text: str) -> list[str]:
    page_prefixes = {w[:5] for w in re.findall(r"[a-z][a-z-]{3,}", page_text.lower())}
    return sorted(topic_words(headline, name) & page_prefixes)


def resolve(name: str, headline: str | None, icp: ICP | None = None) -> Resolution:
    icp = icp or get_icp()
    res = Resolution(domain=None)
    for dom in candidates(name, icp):
        r = fetch(f"https://{dom}/", retries=0, timeout=8, quiet=True)
        attempt = {"domain": dom, "status": r.status, "final_url": r.final_url, "error": r.error}
        res.tried.append(attempt)
        if not r.ok:
            continue
        final_host = (re.sub(r"^https?://", "", r.final_url).split("/")[0]).removeprefix("www.")
        brand = brand_text(r.text)
        attempt["brand"] = brand[:200]
        if PARKED.search(r.text[:20000]):
            attempt["result"] = "parked"
            continue
        if not name_matches(name, brand):
            attempt["result"] = "name_not_in_brand"
            continue
        overlap = topic_overlap(headline, name, BeautifulSoup(r.text, "html.parser").get_text(" ")) if headline else ["manual"]
        attempt["topic_overlap"] = overlap
        if not overlap:
            attempt["result"] = "no_topic_overlap"
            continue
        attempt["result"] = "accepted"
        res.domain = final_host if final_host.endswith(dom.split(".", 1)[1]) else dom
        res.evidence = {"brand_text": brand[:300], "url": r.final_url, "topic_overlap": overlap}
        log.info("resolved %s -> %s (brand: %s)", name, res.domain, brand[:80])
        return res
    log.info("unresolved %s (tried %s)", name, ", ".join(t["domain"] for t in res.tried))
    return res
