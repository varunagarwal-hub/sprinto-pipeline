"""Company website crawler: homepage first, then likely pages discovered from its links.

Budget: max_fetches_per_company in total. The initial crawl leaves max_refetches_per_company
fetches free for the qualifier's agent loop, which calls Scraper.fetch_type() for one page type at a time.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from pipeline import ats
from pipeline.config import ICP, get_icp
from pipeline.http import fetch

log = logging.getLogger(__name__)

STRIP_TAGS = ["script", "style", "noscript", "svg", "iframe", "form", "template", "nav"]
KEEP_FOOTER_TYPES = {"homepage", "trust", "security"}   # footers often carry SOC 2 / ISO badges
SKIP_EXT = re.compile(r"\.(pdf|png|jpe?g|gif|svg|webp|zip|mp4|ics)$", re.I)
BADGE_ALT = re.compile(r"soc ?2|iso ?27001|gdpr|hipaa|pci|certified|compliant|trust", re.I)


@dataclass
class Page:
    url: str
    page_type: str
    text: str
    status: int | None
    ok: bool
    cached: bool = False
    guessed: bool = False
    vendor_hits: list[dict] = field(default_factory=list)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


PHOTO_ALT = re.compile(r"photo|portrait|person|people|smiling|diagram|screen|graphic|cover|illustration|icon", re.I)


def _short_alts(soup: BeautifulSoup) -> list[str]:
    """Short image alt texts are usually customer/partner logos ('Nutanix', 'Box logo')."""
    out = []
    for img in soup.find_all("img"):
        alt = re.sub(r"\s+", " ", img.get("alt", "")).strip()
        alt = re.sub(r"\s+logo$", "", alt, flags=re.I)
        if 1 < len(alt) <= 40 and len(alt.split()) <= 5 and not PHOTO_ALT.search(alt):
            out.append(alt)
    return list(dict.fromkeys(out))[:40]


def clean_text(html: str, page_type: str, max_chars: int) -> str:
    soup = BeautifulSoup(html, "html.parser")
    badges = [img.get("alt", "").strip() for img in soup.find_all("img") if BADGE_ALT.search(img.get("alt", ""))]
    logos = _short_alts(soup)
    for tag in soup(STRIP_TAGS):
        tag.decompose()
    if page_type not in KEEP_FOOTER_TYPES:
        for tag in soup(["footer", "header"]):
            tag.decompose()
    for tag in soup.find_all(attrs={"id": re.compile("cookie|consent", re.I)}):
        tag.decompose()
    seen: set[str] = set()
    lines: list[str] = []
    for line in soup.get_text("\n").splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < 3 or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    lines += [f"[image: {b}]" for b in dict.fromkeys(badges)]
    if logos:
        lines.append(f"[logo images: {', '.join(logos)}]")
    return "\n".join(lines)[:max_chars]


def detect_vendors(html: str, url: str, icp: ICP) -> list[dict]:
    """Trust-center vendors referenced anywhere in the raw HTML (links, scripts, iframes)."""
    hits = []
    low = html.lower()
    for vendor, domains in icp.trust_vendor_domains.items():
        for d in domains:
            i = low.find(d)
            if i >= 0:
                snippet = re.sub(r"\s+", " ", html[max(0, i - 80): i + len(d) + 80])
                hits.append({"vendor": vendor, "matched": d, "url": url, "snippet": snippet})
                break
    return hits


def discover_links(html: str, base_url: str, domain: str, icp: ICP) -> dict[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    vendor_hosts = tuple(d for ds in icp.trust_vendor_domains.values() for d in ds)
    found: dict[str, list[tuple[int, str]]] = {t: [] for t in icp.page_keywords}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        if SKIP_EXT.search(urlparse(url).path):
            continue
        host = _host(url)
        same_site = host == domain or host.endswith("." + domain)
        is_ats = host.endswith(ats.ATS_HOSTS)
        is_vendor = host.endswith(vendor_hosts)
        if not (same_site or is_ats or is_vendor):
            continue
        path = urlparse(url).path.lower()
        text = a.get_text(" ", strip=True).lower()
        for ptype, kws in icp.page_keywords.items():
            if ptype == "careers" and is_ats:
                found[ptype].append((0, url))
                continue
            if ptype == "trust" and (is_vendor or host.startswith("trust.")):
                found[ptype].append((0, url))
                continue
            if not same_site:
                continue
            segs = [s for s in path.split("/") if s]
            if any(s in kws for s in segs):
                found[ptype].append((1 + len(segs), url))           # exact path segment
            elif any(k in path for k in kws) or any(re.search(rf"\b{re.escape(k)}\b", text) for k in kws):
                found[ptype].append((10 + len(segs), url))          # looser match
    return {t: list(dict.fromkeys(u for _, u in sorted(v))) for t, v in found.items()}


class Scraper:
    def __init__(self, domain: str, icp: ICP | None = None):
        self.icp = icp or get_icp()
        self.domain = domain.lower().removeprefix("www.")
        self.pages: list[Page] = []
        self.candidates: dict[str, list[str]] = {}
        self.fetches = 0
        self.tried: set[str] = set()
        self.ats_board: tuple[str, str] | None = None
        self.homepage_url: str | None = None
        self._cap: int | None = None   # temporary lower cap used by initial_crawl

    # -- budget
    @property
    def budget_left(self) -> int:
        cap = self.icp.max_fetches_per_company if self._cap is None else self._cap
        return cap - self.fetches

    def fetched_types(self) -> set[str]:
        return {p.page_type for p in self.pages if p.ok}

    def _get(self, url: str, page_type: str, guessed: bool = False) -> Page | None:
        if self.budget_left <= 0 or url in self.tried:
            return None
        self.tried.add(url)
        self.fetches += 1
        res = fetch(url)
        mc = self.icp.http["max_page_chars"]
        if not res.ok or "html" not in (res.content_type or "html"):
            page = Page(url=res.final_url, page_type=page_type, text="", status=res.status, ok=False,
                        cached=res.cached, guessed=guessed)
        else:
            page = Page(url=res.final_url, page_type=page_type, text=clean_text(res.text, page_type, mc),
                        status=res.status, ok=True, cached=res.cached, guessed=guessed,
                        vendor_hits=detect_vendors(res.text, res.final_url, self.icp))
            self._raw_html = res.text
        self.pages.append(page)
        log.info("[%s] %-9s %s %s (%d chars)%s", self.domain, page_type, res.status, res.final_url,
                 len(page.text), " cached" if res.cached else "")
        return page

    # -- steps
    def fetch_homepage(self) -> Page | None:
        for url in (f"https://{self.domain}/", f"https://www.{self.domain}/"):
            page = self._get(url, "homepage")
            if page and page.ok:
                self.homepage_url = page.url
                self.candidates = discover_links(self._raw_html, page.url, self.domain, self.icp)
                self.ats_board = ats.find_ats(self._raw_html)
                return page
        return None

    def _fetch_ats(self) -> Page | None:
        if not self.ats_board or self.budget_left <= 0:
            return None
        kind, token = self.ats_board
        url = ats.api_url(kind, token)
        if url in self.tried:
            return None
        self.tried.add(url)
        self.fetches += 1
        res = fetch(url)
        if not res.ok:
            page = Page(url=url, page_type="ats", text="", status=res.status, ok=False, cached=res.cached)
        else:
            try:
                jobs = ats.parse_jobs(kind, res.text)
                text = ats.render_jobs(jobs, self.icp.audit_role_keywords, self.icp.ats_max_jobs)
                page = Page(url=url, page_type="ats", text=text, status=res.status, ok=True, cached=res.cached)
            except ValueError as e:
                page = Page(url=url, page_type="ats", text="", status=res.status, ok=False, cached=res.cached)
                log.warning("[%s] ATS parse failed: %s", self.domain, e)
        self.pages.append(page)
        log.info("[%s] ats       %s %s board=%s (%d chars)", self.domain, res.status, url, token, len(page.text))
        return page

    def fetch_type(self, page_type: str) -> Page | None:
        """Fetch the best unfetched page of this type. Careers resolves to the ATS API when possible."""
        if self.budget_left <= 0:
            return None
        if page_type == "careers":
            for url in self.candidates.get("careers", []):
                if url not in self.tried and _host(url).endswith(ats.ATS_HOSTS):
                    self.ats_board = self.ats_board or ats.find_ats(url)
            if self.ats_board and "ats" not in self.fetched_types():
                page = self._fetch_ats()
                if page and page.ok:
                    return page
        for url in self.candidates.get(page_type, []):
            if url in self.tried or _host(url).endswith(ats.ATS_HOSTS):
                continue
            page = self._get(url, page_type)
            if page and page.ok:
                if page_type == "careers" and not self.ats_board:
                    self.ats_board = ats.find_ats(self._raw_html)
                    if self.ats_board and self.budget_left > 0:
                        self._fetch_ats()
                return page
        # Fallback: guess the conventional path once.
        guess = f"https://{self.domain}/{page_type}"
        if guess not in self.tried:
            page = self._get(guess, page_type, guessed=True)
            if page and page.ok:
                return page
        return None

    def initial_crawl(self) -> list[Page]:
        """Homepage + highest-priority pages, keeping max_refetches_per_company fetches for the agent loop."""
        self._cap = self.icp.max_fetches_per_company - self.icp.max_refetches_per_company
        try:
            if not self.fetch_homepage():
                return self.pages
            for ptype in self.icp.initial_page_order:
                if self.budget_left <= 0:
                    break
                if ptype not in self.fetched_types():
                    self.fetch_type(ptype)
        finally:
            self._cap = None
        return self.pages

    def ok_pages(self) -> list[Page]:
        return [p for p in self.pages if p.ok and p.text]
