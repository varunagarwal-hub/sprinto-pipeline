"""Record every external call in api_usage. Never raises: logging must not break the pipeline."""
from __future__ import annotations

import logging
from contextvars import ContextVar

log = logging.getLogger(__name__)

current_run_id: ContextVar[int | None] = ContextVar("current_run_id", default=None)
current_company_id: ContextVar[int | None] = ContextVar("current_company_id", default=None)
current_stage: ContextVar[str | None] = ContextVar("current_stage", default=None)


def log_usage(
    service: str,
    *,
    stage: str | None = None,
    model: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    credits_used: int | None = None,
    credits_remaining: int | None = None,
    cached: bool = False,
    ok: bool = True,
    error: str | None = None,
) -> None:
    from pipeline import db  # local import keeps http/llm importable without a DB

    try:
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO api_usage (run_id, company_id, service, stage, model, tokens_in, tokens_out,
                                          credits_used, credits_remaining, cached, ok, error)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (current_run_id.get(), current_company_id.get(), service, stage or current_stage.get(),
                 model, tokens_in, tokens_out, credits_used, credits_remaining, cached, ok,
                 (error or "")[:500] or None),
            )
    except Exception as e:  # noqa: BLE001
        log.warning("api_usage insert failed: %s", e)
