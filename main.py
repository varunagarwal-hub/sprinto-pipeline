"""CLI entry point.

    python main.py migrate                 apply SQL migrations to Neon
    python main.py check                   validate icp.json + .env, list DB tables
    python main.py stage1                  (build step 2) news -> ranked pool
    python main.py run                     (build step 4) batch research loop
    python main.py --domains domains.txt   (build step 2) inject manual domains into the pool
"""
from __future__ import annotations

import argparse
import logging
import sys

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


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Sprinto cold outreach pipeline")
    parser.add_argument("command", nargs="?", choices=["migrate", "check", "stage1", "run"])
    parser.add_argument("--domains", help="file of manual domains to inject into the pool")
    args = parser.parse_args(argv)

    if args.command == "migrate":
        return cmd_migrate()
    if args.command == "check":
        return cmd_check()
    if args.command in ("stage1", "run") or args.domains:
        print("Not implemented yet (later build step).")
        return 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
