"""CLI entry point.

    python main.py migrate                 apply SQL migrations to Neon
    python main.py check                   validate icp.json + .env, list DB tables
    python main.py stage1                  news -> gates -> ranked pool, prints top 20
    python main.py pool                    print the top 20 unseen companies in the pool
    python main.py --domains domains.txt   inject manual domains (domain[,name[,Series X]]) at top rank
    python main.py run                     batches of 20 until 5 qualified (icp.json), max 3 batches
    python main.py extract <domain>        crawl + extract one domain, print evidence JSON
    python main.py poc                     find a PoC for qualified companies without one (HUNTER_MODE)
    python main.py write [company_id]      draft emails (stored in Neon) for companies with a PoC
    python main.py send <email_id>         send one draft via Resend to MY_EMAIL only
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from pipeline import db
from pipeline.config import get_icp, get_settings

log = logging.getLogger("sprinto")


def cmd_migrate() -> int:
    applied = db.migrate()
    print(f"Applied: {', '.join(applied)}" if applied else "Schema already up to date.")
    print("Tables:", ", ".join(db.list_tables()))
    return 0


def cmd_check() -> int:
    icp = get_icp()
    s = get_settings()
    print(f"icp.json OK: batch_size={icp.batch_size}, target={icp.target_qualified_per_run}, "
          f"stages={icp.funding_stages}")
    for name, val in [("GEMINI_API_KEY", s.gemini_api_key), ("HUNTER_API_KEY", s.hunter_api_key),
                      ("DATABASE_URL", s.database_url), ("MY_EMAIL", s.my_email)]:
        print(f"  {name:<15} {'set' if val else 'MISSING'}")
    print(f"  GEMINI_MODEL    {s.gemini_model}")
    print(f"  PIPELINE_ENV    {s.env}")
    print("Tables:", ", ".join(db.list_tables()) or "(none)")
    return 0


def print_pool(n: int = 20) -> None:
    from pipeline.stage1 import top_of_pool

    rows = top_of_pool(n)
    print(f"\nTop {len(rows)} of the ranked pool (unseen):")
    print(f"{'#':>2}  {'score':>6}  {'rec':>5} {'ind':>5} {'bst':>5} {'saas':>5}  {'stage':<8} {'date':<10}  "
          f"{'company':<22} headline")
    for i, r in enumerate(rows, 1):
        b = r["rank_breakdown"] or {}
        when = r["funding_date"].isoformat() if r["funding_date"] else "manual"
        print(f"{i:>2}  {r['rank_score']:>6.3f}  {b.get('recency', 0):>5.2f} {b.get('industry', 0):>5.2f} "
              f"{b.get('boost', 0):>5.2f} {b.get('saas', 0):>5.2f}  {r['funding_stage'] or '?':<8} {when:<10}  "
              f"{r['name'][:22]:<22} {(r['headline'] or '(manual domain)')[:70]}")


def cmd_stage1() -> int:
    from pipeline.stage1 import run_stage1

    summary = run_stage1()
    print("\nStage 1 summary:")
    for k, v in summary.items():
        print(f"  {k:<16} {v}")
    print_pool()
    return 0


def cmd_domains(path: str) -> int:
    from pipeline.stage1 import inject_domains

    stats = inject_domains(Path(path))
    print(f"Manual domains: {stats}")
    print_pool()
    return 0


def cmd_extract(domain: str, name: str | None) -> int:
    """Build-step-3 demo: crawl one domain and print the verified evidence JSON."""
    import json

    from pipeline.extract import extract
    from pipeline.scraper import Scraper

    s = Scraper(domain)
    pages = s.initial_crawl()
    print(f"\nFetched {s.fetches}/{s.icp.max_fetches_per_company} "
          f"(reserve {s.icp.max_refetches_per_company} for the agent loop):")
    for p in pages:
        print(f"  {p.page_type:<9} {p.status} {'ok ' if p.ok else 'ERR'} {len(p.text):>6} chars  {p.url}")
    print("Discovered candidates:", {k: len(v) for k, v in s.candidates.items()}, "| ATS:", s.ats_board)
    evidence = extract(name or domain.split(".")[0].capitalize(), domain, pages)
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    return 0 if evidence else 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "urllib3", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Sprinto cold outreach pipeline")
    parser.add_argument("command", nargs="?", choices=["migrate", "check", "stage1", "pool", "extract", "run", "poc", "write", "send"])
    parser.add_argument("target", nargs="?", help="domain for the extract command")
    parser.add_argument("--domains", help="file of manual domains to inject into the pool")
    parser.add_argument("--name", help="company name for the extract command")
    parser.add_argument("--target-qualified", type=int, help="override target_qualified_per_run for this run")
    parser.add_argument("--max-batches", type=int, help="override max_batches_per_run for this run")
    args = parser.parse_args(argv)

    if args.domains:
        return cmd_domains(args.domains)
    if args.command == "migrate":
        return cmd_migrate()
    if args.command == "check":
        return cmd_check()
    if args.command == "stage1":
        return cmd_stage1()
    if args.command == "extract":
        if not args.target:
            parser.error("extract needs a domain, e.g. python main.py extract findem.ai --name Findem")
        return cmd_extract(args.target, args.name)
    if args.command == "poc":
        from pipeline.config import get_settings
        from pipeline.poc import credits_remaining, run_pending

        live = get_settings().hunter_mode == "live"
        print(f"Hunter mode: {'LIVE (spends credits)' if live else 'mock (no credits spent)'}; "
              f"searches left: {credits_remaining(live)}")
        for r in run_pending():
            print(f"  {r['company']:<20} {r['status']}")
        return 0
    if args.command == "write":
        from pipeline.write import run_pending as write_drafts

        cid = int(args.target) if args.target else None
        for r in write_drafts(company_id=cid):
            print(f"  {r['company']:<20} {r['status']}")
        return 0
    if args.command == "send":
        from pipeline.mailer import send_email

        if not args.target:
            parser.error("send needs an email id, e.g. python main.py send 3")
        print(send_email(int(args.target)))
        return 0
    if args.command == "pool":
        print_pool()
        return 0
    if args.command == "run":
        from pipeline.batch import run

        summary = run(target=args.target_qualified, max_batches=args.max_batches)
        print("\nRun summary:")
        for k, v in summary.items():
            print(f"  {k:<14} {v}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
