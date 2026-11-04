"""CLI entry point.

    python main.py migrate                 apply SQL migrations to Neon
    python main.py check                   validate icp.json + .env, list DB tables
    python main.py stage1                  news -> gates -> ranked pool, prints top 20
    python main.py pool                    print the top 20 unseen companies in the pool
    python main.py --domains domains.txt   inject manual domains (domain[,name[,Series X]]) at top rank
    python main.py run                     (build step 4) batch research loop
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


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Sprinto cold outreach pipeline")
    parser.add_argument("command", nargs="?", choices=["migrate", "check", "stage1", "pool", "run"])
    parser.add_argument("--domains", help="file of manual domains to inject into the pool")
    args = parser.parse_args(argv)

    if args.domains:
        return cmd_domains(args.domains)
    if args.command == "migrate":
        return cmd_migrate()
    if args.command == "check":
        return cmd_check()
    if args.command == "stage1":
        return cmd_stage1()
    if args.command == "pool":
        print_pool()
        return 0
    if args.command == "run":
        print("Not implemented yet (build step 4).")
        return 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
