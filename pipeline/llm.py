"""Gemini wrapper: structured JSON output, on-disk cache in dev, retries, token logging.

Gemini only extracts and writes. It never makes a pipeline decision.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from pipeline.config import CACHE_DIR, get_settings
from pipeline.usage import log_usage

log = logging.getLogger(__name__)

LLM_CACHE = CACHE_DIR / "gemini"
T = TypeVar("T", bound=BaseModel)
_client: genai.Client | None = None


@dataclass
class LLMResult:
    data: BaseModel | None
    raw_text: str
    tokens_in: int
    tokens_out: int
    cached: bool
    error: str | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        key = get_settings().gemini_api_key
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set in .env")
        _client = genai.Client(api_key=key)
    return _client


def _cache_key(model: str, system: str, prompt: str, schema: type[BaseModel]) -> str:
    blob = json.dumps([model, system, prompt, schema.model_json_schema()], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def generate_json(prompt: str, schema: type[T], *, system: str, stage: str,
                  temperature: float = 0.0, use_cache: bool | None = None) -> LLMResult:
    """Call Gemini in JSON mode with a pydantic response schema. Never raises on API failure."""
    settings = get_settings()
    model = settings.gemini_model
    if use_cache is None:
        use_cache = settings.is_dev
    path = LLM_CACHE / f"{_cache_key(model, system, prompt, schema)}.json"
    if use_cache and path.exists():
        c = json.loads(path.read_text(encoding="utf-8"))
        log_usage("gemini", stage=stage, model=model, tokens_in=c["tokens_in"], tokens_out=c["tokens_out"],
                  cached=True)
        return LLMResult(schema.model_validate_json(c["text"]), c["text"], c["tokens_in"], c["tokens_out"], True)

    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=temperature,
    )
    last_err = None
    for attempt in range(3):          # first try + 2 retries
        try:
            resp = _get_client().models.generate_content(model=model, contents=prompt, config=config)
            um = resp.usage_metadata
            t_in = (um.prompt_token_count or 0) if um else 0
            t_out = ((um.candidates_token_count or 0) + (getattr(um, "thoughts_token_count", 0) or 0)) if um else 0
            text = resp.text or ""
            data = schema.model_validate_json(text)
            log_usage("gemini", stage=stage, model=model, tokens_in=t_in, tokens_out=t_out)
            if use_cache:
                LLM_CACHE.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"text": text, "tokens_in": t_in, "tokens_out": t_out}), encoding="utf-8")
            return LLMResult(data, text, t_in, t_out, False)
        except errors.APIError as e:
            last_err = f"APIError {e.code}: {e.message}"
            if e.code not in (429, 500, 502, 503, 504):
                break
        except ValueError as e:      # schema validation of the model's JSON
            last_err = f"invalid JSON from model: {e}"
        if attempt < 2:
            time.sleep(2 * (2 ** attempt))
    log.warning("Gemini call failed (%s): %s", stage, last_err)
    log_usage("gemini", stage=stage, model=model, ok=False, error=last_err)
    return LLMResult(None, "", 0, 0, False, last_err)
