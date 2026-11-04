"""Neon/Postgres access: connection helper and a tiny SQL migration runner (no ORM)."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from pipeline.config import ROOT, get_settings

MIGRATIONS_DIR = ROOT / "migrations"


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    url = get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=15) as conn:
        yield conn


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def migrate() -> list[str]:
    """Apply every migrations/*.sql not yet recorded in schema_migrations. Returns applied names."""
    applied: list[str] = []
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   version    text PRIMARY KEY,
                   applied_at timestamptz NOT NULL DEFAULT now()
               )"""
        )
        conn.commit()
        done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        for path in _migration_files():
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied


def list_tables() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT table_name FROM information_schema.tables
               WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
               ORDER BY table_name"""
        ).fetchall()
    return [r["table_name"] for r in rows]
