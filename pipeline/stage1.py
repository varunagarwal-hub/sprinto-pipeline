"""Stage 1: Google News RSS -> rule-based gates -> ranked pool in Neon.

No LLM calls and no website scraping here. Everything is regex + icp.json.
Pure functions (parse_*, detect_*, evaluate_item, rank) are unit-tested; run_stage1() does I/O.
"""
from __future__ import annotations

import calendar
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

import feedparser
from psycopg.types.json import Jsonb

from pipeline import db
from pipeline.config import ICP, get_icp
from pipeline.http import fetch
from pipeline.usage import log_usage

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- headline parsing

FUNDING_VERBS = (
    r"raises|raised|secures|secured|bags|bagged|lands|landed|closes|closed|nabs|nets|gets|scores|"
    r"snags|picks\s+up|rakes\s+in|announces|completes|receives|garners|mops\s+up|pulls\s+in|"
    r"attracts|grabs|banks|hauls\s+in|pockets|reaches|hits|achieves|doubles|exits\s+stealth"
)
_VERB_RE = re.compile(rf"^(?P<pre>.+?)\s+(?:{FUNDING_VERBS})\b", re.IGNORECASE)

# Words that describe a company rather than name it ("SaaS startup Growfin", "US fintech Meow").
DESCRIPTOR_WORDS = {
    "startup", "startups", "start-up", "start-ups", "platform", "firm", "company", "unicorn", "provider",
    "maker", "player", "brand", "aggregator", "marketplace", "app", "solution", "solutions", "developer",
    "operator", "lender", "neobank", "insurer", "outfit", "specialist", "venture", "tech", "saas", "b2b",
    "fintech", "insurtech", "healthtech", "edtech", "proptech", "legaltech", "regtech", "agritech",
    "deeptech", "hrtech", "martech", "cleantech", "climatetech", "lendtech", "ai", "software",
    "spinout", "spin-out", "spinoff", "spin-off", "biz", "arm", "unit", "subsidiary",
}
# A name may legitimately end in these ("Drivetrain AI"); any other descriptor as the last token
# means we parsed a description, not a name ("Cincinnati fintech").
NAME_SAFE_TERMINALS = {"ai", "tech", "software", "app", "solutions", "labs", "b2b", "saas"}
# Leading place/nationality words are stripped ("Mexican Plenna" -> "Plenna").
LEADING_PLACE_WORDS = {
    "us", "u.s.", "uk", "u.k.", "american", "indian", "india", "british", "canadian", "mexican", "brazilian",
    "argentinian", "argentine", "chilean", "colombian", "peruvian", "latam", "israeli", "saudi", "emirati",
    "uae", "egyptian", "african", "nigerian", "kenyan", "south", "german", "french", "dutch", "swedish",
    "norwegian", "danish", "finnish", "nordic", "spanish", "italian", "swiss", "irish", "european",
    "chinese", "japanese", "korean", "singaporean", "sg", "indonesian", "vietnamese", "australian",
    "aussie", "bengaluru", "bangalore", "mumbai", "delhi", "gurugram", "noida", "pune", "hyderabad",
    "chennai", "london", "berlin", "paris", "new", "york", "nyc", "sf", "silicon", "valley", "boston",
}
_POSSESSIVE_RE = re.compile(r"[’']s$", re.IGNORECASE)
_SEED_BEFORE_SERIES_RE = re.compile(r"\bseed\b.*\bseries\b", re.IGNORECASE)
DESCRIPTOR_SUFFIXES = ("-based", "-backed", "-headquartered", "-born", "-focused", "-led", "-founded")
NOT_A_NAME = {
    "startup", "startups", "fintech", "company", "companies", "this", "how", "why", "what", "india",
    "report", "exclusive", "funding", "saas", "the", "a", "an", "its", "it", "who", "firm", "platform",
}
LEGAL_SUFFIXES = {
    "inc", "ltd", "limited", "llc", "pvt", "private", "technologies", "technology", "tech", "labs",
    "ai", "io", "hq", "corp", "co", "software", "solutions", "systems", "gmbh", "plc",
}

_STAGE_RE = re.compile(r"(?<!pre-)(?<!pre )\bseries[\s-]+([a-h])\b", re.IGNORECASE)
_AMOUNT_RE = re.compile(
    r"(?:US\$|USD|\$|€|£|₹|INR|Rs\.?)\s?\d+(?:[.,]\d+)?\s?(?:mn|mln|million|m|bn|billion|b|cr|crore|k)?\b"
    r"|\b\d+(?:\.\d+)?\s?(?:million|mn|mln|crore|cr|billion|bn)\b",
    re.IGNORECASE,
)


def strip_publisher(title: str, publisher: str | None = None) -> str:
    """Google News appends ' - Publisher' to every title."""
    if publisher and title.endswith(f" - {publisher}"):
        return title[: -len(publisher) - 3].strip()
    head, sep, _tail = title.rpartition(" - ")
    return head.strip() if sep else title.strip()


def _is_descriptor(token: str) -> bool:
    t = token.lower().strip(",'’\"")
    return t in DESCRIPTOR_WORDS or t.endswith(DESCRIPTOR_SUFFIXES)


def parse_company_name(headline: str) -> str | None:
    """'Logistics startup OORJAA raises Series A ...' -> 'OORJAA'. None if no confident parse."""
    m = _VERB_RE.match(headline.strip())
    if not m:
        return None
    pre = m.group("pre")
    if ":" in pre:                       # "Exclusive: Acme raises"
        pre = pre.rsplit(":", 1)[1]
    if "," in pre:                       # "Acme, a fintech startup, raises"
        pre = pre.split(",", 1)[0]
    tokens = pre.split()
    if not tokens:
        return None
    # Strip everything up to the last descriptor or possessive, but never the final token
    # ("SaaS startup Drivetrain AI" -> "Drivetrain AI", "Norway's Unleash" -> "Unleash").
    cut = -1
    for i, tok in enumerate(tokens[:-1]):
        if _is_descriptor(tok) or _POSSESSIVE_RE.search(tok):
            cut = i
    tokens = tokens[cut + 1:]
    while len(tokens) > 1 and tokens[0].lower().strip(",") in LEADING_PLACE_WORDS:
        tokens = tokens[1:]
    if _is_descriptor(tokens[-1]) and tokens[-1].lower() not in NAME_SAFE_TERMINALS:
        return None
    name = " ".join(tokens).strip(" \"'“”‘’")
    name = _POSSESSIVE_RE.sub("", name).strip()
    if not name or len(name.split()) > 4:
        return None
    if name.lower() in NOT_A_NAME or not name[0].isalnum():
        return None
    if len(tokens) == 1 and _is_descriptor(tokens[0]):
        return None
    return name


def detect_stage(text: str) -> str | None:
    m = _STAGE_RE.search(text)
    return f"Series {m.group(1).upper()}" if m else None


def detect_amount(text: str) -> str | None:
    m = _AMOUNT_RE.search(text)
    return m.group(0).strip() if m else None


def norm_name(name: str) -> str:
    s = name.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    tokens = s.split()
    while len(tokens) > 1 and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    low = text.lower()
    return [k for k in keywords if re.search(rf"(?<![a-z0-9]){re.escape(k.lower())}(?![a-z0-9])", low)]


# ---------------------------------------------------------------- gates + rank

@dataclass
class NewsItem:
    headline: str
    publisher: str
    url: str
    published: date
    query: str
    region: str


@dataclass
class Candidate:
    name: str
    norm: str
    stage: str
    item: NewsItem
    amount: str | None
    age_days: int
    industry_tags: list[str]
    rank_score: float
    rank_breakdown: dict[str, Any]
    gates: list[dict[str, Any]]
    other_items: list[NewsItem] = field(default_factory=list)


def industry_match(text: str, icp: ICP) -> tuple[float, list[str]]:
    tags = [ind for ind, aliases in icp.industry_aliases.items() if keyword_hits(text, aliases)]
    weight = max((icp.industry_weights.get(t, icp.industry_weights["default"]) for t in tags),
                 default=icp.industry_weights["default"])
    return weight, tags


def rank(item: NewsItem, age_days: int, icp: ICP) -> tuple[float, dict[str, Any], list[str], list[str]]:
    w = icp.rank_weights
    max_days = icp.funding_max_age_months * 30.44
    recency = max(0.0, 1.0 - age_days / max_days)
    ind_weight, tags = industry_match(item.headline, icp)
    boosts = keyword_hits(item.headline, icp.rank_boost_keywords)
    saas = keyword_hits(item.headline, icp.saas_keywords)
    parts = {
        "recency": round(w["recency"] * recency, 4),
        "industry": round(w["industry"] * ind_weight, 4),
        "boost": round(min(w["boost_cap"], w["boost_per_hit"] * len(boosts)), 4),
        "saas": round(w["saas_hit"] if saas else 0.0, 4),
    }
    breakdown = {**parts, "inputs": {"age_days": age_days, "industry_weight": ind_weight,
                                     "industry_tags": tags, "boost_hits": boosts, "saas_hits": saas}}
    return round(sum(parts.values()), 4), breakdown, tags, saas


def evaluate_item(item: NewsItem, icp: ICP, today: date) -> tuple[Candidate | None, str | None]:
    """Apply Stage 1 gates to one news item. Returns (candidate, None) or (None, drop_reason)."""
    ev = {"headline": item.headline, "url": item.url, "date": item.published.isoformat(),
          "publisher": item.publisher}

    stage = detect_stage(item.headline)
    if not stage:
        return None, "no_funding_stage"
    if stage not in icp.funding_stages:
        return None, f"stage_out_of_range:{stage}"
    if _SEED_BEFORE_SERIES_RE.search(item.headline):
        return None, "seed_round"   # "Raises seed round; targets Series A next year"

    age_days = (today - item.published).days
    if age_days > icp.funding_max_age_months * 30.44:
        return None, "article_too_old"

    name = parse_company_name(item.headline)
    if not name:
        return None, "unparseable_name"

    excluded = keyword_hits(item.headline, icp.exclude_keywords)
    if excluded:
        return None, f"excluded_keyword:{excluded[0]}"

    score, breakdown, tags, saas = rank(item, age_days, icp)
    gates = [
        {"gate": "name_parse", "result": "pass", "value": name, **ev},
        {"gate": "funding_stage", "result": "pass", "value": stage, **ev},
        {"gate": "funding_recency", "result": "pass", "value": age_days,
         "reason": f"{age_days} days old (max {icp.funding_max_age_months} months)", **ev},
        {"gate": "exclude_keywords", "result": "pass", "value": [], **ev},
        {"gate": "saas_keyword", "result": "pass" if saas else "unknown", "value": saas,
         "reason": None if saas else "no SaaS keyword in headline; Stage 2 confirms", **ev},
    ]
    return Candidate(name=name, norm=norm_name(name), stage=stage, item=item,
                     amount=detect_amount(item.headline), age_days=age_days, industry_tags=tags,
                     rank_score=score, rank_breakdown=breakdown, gates=gates), None


def dedupe(candidates: list[Candidate]) -> list[Candidate]:
    """One candidate per normalised name: keep the highest-scoring article, remember the rest."""
    best: dict[str, Candidate] = {}
    for c in sorted(candidates, key=lambda c: c.rank_score, reverse=True):
        if c.norm in best:
            keep = best[c.norm]
            if all(o.url != c.item.url for o in keep.other_items) and keep.item.url != c.item.url:
                keep.other_items.append(c.item)
        else:
            best[c.norm] = c
    return sorted(best.values(), key=lambda c: c.rank_score, reverse=True)


# ---------------------------------------------------------------- I/O

def build_feed_urls(icp: ICP) -> list[tuple[str, str, str]]:
    out = []
    for q in icp.news_queries:
        full_q = f"{q} when:{icp.news_when}" if icp.news_when else q
        for r in icp.news_regions:
            url = (f"https://news.google.com/rss/search?q={quote_plus(full_q)}"
                   f"&hl={r['hl']}&gl={r['gl']}&ceid={r['ceid']}")
            out.append((q, r["name"], url))
    return out


def fetch_news(icp: ICP) -> tuple[list[NewsItem], int]:
    items: list[NewsItem] = []
    feeds = build_feed_urls(icp)
    for query, region, url in feeds:
        res = fetch(url)
        log_usage("google_news", stage="stage1", cached=res.cached, ok=res.ok, error=res.error)
        if not res.ok:
            continue
        parsed = feedparser.parse(res.text)
        for e in parsed.entries:
            if not getattr(e, "published_parsed", None):
                continue
            publisher = (e.get("source") or {}).get("title", "")
            items.append(NewsItem(
                headline=strip_publisher(e.title, publisher),
                publisher=publisher,
                url=e.link,
                published=datetime.fromtimestamp(calendar.timegm(e.published_parsed), tz=timezone.utc).date(),
                query=query,
                region=region,
            ))
        log.info("feed %-45s %-6s %3d items", query[:45], region, len(parsed.entries))
    return items, len(feeds)


def _unique_items(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    out = []
    for it in items:
        key = re.sub(r"\W+", " ", it.headline.lower()).strip()
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _stage1_evidence(c: Candidate) -> list[dict[str, Any]]:
    others = [{"headline": o.headline, "url": o.url, "date": o.published.isoformat(), "publisher": o.publisher}
              for o in c.other_items[:5]]
    return c.gates + ([{"gate": "other_coverage", "result": "info", "articles": others}] if others else [])


def _write_decisions(conn, company_id: int, gates: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM decisions WHERE company_id = %s AND stage = 'stage1' AND decided_by = 'rule'",
                 (company_id,))
    for g in gates:
        if g["gate"] == "other_coverage":
            continue
        conn.execute(
            """INSERT INTO decisions (company_id, stage, gate, result, value, evidence_quote, source_url,
                                      evidence_date, reason)
               VALUES (%s, 'stage1', %s, %s, %s, %s, %s, %s, %s)""",
            (company_id, g["gate"], g["result"], Jsonb(g.get("value")), g.get("headline"), g.get("url"),
             g.get("date"), g.get("reason")),
        )


def upsert_pool(conn, candidates: list[Candidate], icp: ICP) -> Counter:
    stats: Counter = Counter()
    cooldown = timedelta(days=icp.seen_cooldown_days)
    now = datetime.now(timezone.utc)
    for c in candidates:
        row = conn.execute("SELECT id, status, source, seen_at FROM companies WHERE norm_name = %s",
                           (c.norm,)).fetchone()
        values = (c.name, c.stage, c.item.published, c.amount, c.industry_tags, c.rank_score,
                  Jsonb(c.rank_breakdown), Jsonb(_stage1_evidence(c)))
        if row is None:
            cid = conn.execute(
                """INSERT INTO companies (name, norm_name, source, funding_stage, funding_date, funding_amount,
                                          industry_tags, rank_score, rank_breakdown, stage1_evidence)
                   VALUES (%s, %s, 'news', %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (c.name, c.norm, *values[1:]),
            ).fetchone()["id"]
            stats["pooled_new"] += 1
        elif row["seen_at"] and now - row["seen_at"] < cooldown:
            stats["skipped_seen"] += 1
            continue
        elif row["source"] == "manual":
            stats["skipped_manual"] += 1
            continue
        else:
            # Still pooled, or researched long enough ago that the cooldown has passed: refresh.
            cid = row["id"]
            conn.execute(
                """UPDATE companies SET name=%s, funding_stage=%s, funding_date=%s, funding_amount=%s,
                          industry_tags=%s, rank_score=%s, rank_breakdown=%s, stage1_evidence=%s,
                          status='pooled', updated_at=now()
                   WHERE id=%s""",
                (*values, cid),
            )
            stats["pooled_updated"] += 1
        _write_decisions(conn, cid, c.gates)
    return stats


def run_stage1(today: date | None = None) -> dict[str, Any]:
    icp = get_icp()
    today = today or datetime.now(timezone.utc).date()
    with db.connect() as conn:
        s1_id = conn.execute("INSERT INTO stage1_runs DEFAULT VALUES RETURNING id").fetchone()["id"]
        conn.commit()

    raw, n_feeds = fetch_news(icp)
    items = _unique_items(raw)
    kept, drops = [], []
    for it in items:
        cand, reason = evaluate_item(it, icp, today)
        if cand:
            kept.append(cand)
        else:
            drops.append({"headline": it.headline, "url": it.url, "date": it.published.isoformat(),
                          "reason": reason})
            if reason == "unparseable_name":
                log.info("dropped (unparseable): %s", it.headline)
    candidates = dedupe(kept)
    drop_counts = Counter(d["reason"].split(":")[0] for d in drops)

    with db.connect() as conn:
        stats = upsert_pool(conn, candidates, icp)
        conn.execute(
            """UPDATE stage1_runs SET finished_at=now(), queries=%s, items_fetched=%s, items_unique=%s,
                      companies_kept=%s, pooled_new=%s, pooled_updated=%s, skipped_seen=%s,
                      drop_counts=%s, drops=%s WHERE id=%s""",
            (n_feeds, len(raw), len(items), len(candidates), stats["pooled_new"], stats["pooled_updated"],
             stats["skipped_seen"], Jsonb(dict(drop_counts)), Jsonb(drops), s1_id),
        )
    return {"feeds": n_feeds, "items_fetched": len(raw), "items_unique": len(items),
            "companies_kept": len(candidates), "drop_counts": dict(drop_counts), **stats}


# ---------------------------------------------------------------- manual domains

def normalise_domain(raw: str) -> str:
    raw = raw.strip().lower()
    host = urlparse(raw if "//" in raw else f"//{raw}").hostname or ""
    return host.removeprefix("www.")


def inject_domains(path: Path) -> dict[str, Any]:
    """Lines: domain[,company name[,Series X]]. '#' starts a comment. Goes to the top of the pool."""
    icp = get_icp()
    cooldown = timedelta(days=icp.seen_cooldown_days)
    now = datetime.now(timezone.utc)
    stats: Counter = Counter()
    with db.connect() as conn:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            domain = normalise_domain(parts[0])
            if not domain or "." not in domain:
                log.warning("line %d: bad domain %r", lineno, parts[0])
                stats["bad_line"] += 1
                continue
            name = parts[1] if len(parts) > 1 and parts[1] else domain.split(".")[0].capitalize()
            stage = parts[2] if len(parts) > 2 and parts[2] else None
            if stage and stage not in icp.funding_stages:
                log.warning("line %d: stage %r not in %s; storing as unknown", lineno, stage, icp.funding_stages)
                stage = None
            norm = norm_name(name)
            gate = {"gate": "manual_injection", "result": "pass", "value": {"domain": domain, "stage": stage},
                    "headline": None, "url": None, "date": now.date().isoformat(),
                    "reason": f"injected from {path.name} line {lineno}"}
            row = conn.execute("SELECT id, seen_at FROM companies WHERE norm_name=%s OR domain=%s",
                               (norm, domain)).fetchone()
            if row and row["seen_at"] and now - row["seen_at"] < cooldown:
                log.warning("%s was researched on %s; inside cooldown, skipped", domain, row["seen_at"].date())
                stats["skipped_seen"] += 1
                continue
            score = icp.rank_weights["manual_domain_score"]
            if row:
                cid = row["id"]
                conn.execute(
                    """UPDATE companies SET domain=%s, domain_source='manual', source='manual',
                              funding_stage=COALESCE(%s, funding_stage), rank_score=%s,
                              stage1_evidence = stage1_evidence || %s, status='pooled', updated_at=now()
                       WHERE id=%s""",
                    (domain, stage, score, Jsonb([gate]), cid),
                )
                stats["updated"] += 1
            else:
                cid = conn.execute(
                    """INSERT INTO companies (name, norm_name, domain, domain_source, source, funding_stage,
                                              rank_score, rank_breakdown, stage1_evidence)
                       VALUES (%s,%s,%s,'manual','manual',%s,%s,%s,%s) RETURNING id""",
                    (name, norm, domain, stage, score, Jsonb({"manual": score}), Jsonb([gate])),
                ).fetchone()["id"]
                stats["inserted"] += 1
            conn.execute(
                """INSERT INTO decisions (company_id, stage, gate, result, value, evidence_date, reason)
                   VALUES (%s, 'stage1', 'manual_injection', 'pass', %s, %s, %s)""",
                (cid, Jsonb(gate["value"]), gate["date"], gate["reason"]),
            )
    return dict(stats)


def top_of_pool(n: int = 20) -> list[dict[str, Any]]:
    icp = get_icp()
    with db.connect() as conn:
        return conn.execute(
            """SELECT id, name, funding_stage, funding_date, funding_amount, rank_score, rank_breakdown,
                      industry_tags, source, stage1_evidence->0->>'headline' AS headline
               FROM companies
               WHERE status='pooled'
                 AND (seen_at IS NULL OR seen_at < now() - make_interval(days => %s))
               ORDER BY rank_score DESC, funding_date DESC NULLS LAST
               LIMIT %s""",
            (icp.seen_cooldown_days, n),
        ).fetchall()
