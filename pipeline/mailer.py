"""Resend delivery. The recipient is always MY_EMAIL: there is deliberately no 'to' parameter,
so nothing in the pipeline can email a real prospect."""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import requests
from rapidfuzz.distance import Levenshtein

from pipeline import db
from pipeline.config import get_settings
from pipeline.usage import current_company_id, log_usage

log = logging.getLogger(__name__)
RESEND_URL = "https://api.resend.com/emails"


def edit_rate(original: str, sent: str) -> float:
    """Normalised Levenshtein distance: 0 = sent unchanged, 1 = completely rewritten."""
    return round(Levenshtein.normalized_distance(original or "", sent or ""), 4)


def subject_line(poc_name: str, company: str, subject: str) -> str:
    return f"[DRAFT for {poc_name} @ {company}] {subject}"


def _post(payload: dict[str, Any], idempotency_key: str) -> tuple[str | None, str | None]:
    s = get_settings()
    err = None
    for attempt in range(3):
        try:
            r = requests.post(RESEND_URL, json=payload, timeout=15, headers={
                "Authorization": f"Bearer {s.resend_api_key}", "Idempotency-Key": idempotency_key})
            if r.ok:
                return r.json().get("id"), None
            err = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        except requests.RequestException as e:
            err = type(e).__name__
        if attempt < 2:
            time.sleep(1.5 * 2 ** attempt)
    return None, err


def send_email(email_id: int, body: str | None = None, subject: str | None = None) -> dict[str, Any]:
    """Send the (possibly human-edited) draft to MY_EMAIL and store both versions + edit rate."""
    s = get_settings()
    if not s.resend_api_key or not s.my_email:
        raise RuntimeError("Set RESEND_API_KEY and MY_EMAIL in .env before sending.")
    with db.connect() as conn:
        e = conn.execute(
            """SELECT e.*, c.name AS company, c.id AS cid, k.full_name AS poc FROM emails e
               JOIN companies c ON c.id=e.company_id LEFT JOIN contacts k ON k.id=e.contact_id
               WHERE e.id=%s""", (email_id,)).fetchone()
    if not e:
        raise ValueError(f"email {email_id} not found")
    if e["status"] == "sent":
        raise ValueError(f"email {email_id} was already sent at {e['sent_at']}")
    current_company_id.set(e["cid"])
    body = body if body is not None else e["original_body"]
    subj = subject_line(e["poc"] or "?", e["company"], subject or e["subject"])
    msg_id, err = _post({"from": s.resend_from, "to": [s.my_email], "subject": subj, "text": body},
                        idempotency_key=f"sprinto-email-{email_id}-{hashlib.sha256(body.encode()).hexdigest()[:12]}")
    log_usage("resend", stage="send", ok=err is None, error=err)
    if err:
        raise RuntimeError(f"Resend send failed: {err}")
    rate = edit_rate(e["original_body"], body)
    with db.connect() as conn:
        conn.execute(
            """UPDATE emails SET sent_body=%s, sent_subject=%s, sent_to=%s, edit_distance=%s,
                      provider_message_id=%s, status='sent', sent_at=now() WHERE id=%s""",
            (body, subj, s.my_email, rate, msg_id, email_id))
        conn.execute("UPDATE companies SET status='sent', updated_at=now() WHERE id=%s", (e["cid"],))
    # CAPTURE: the reviewer's edit is the strongest writing signal we get
    from pipeline.feedback import capture
    capture("draft_sent", "reviewer", e["cid"], email_id,
            payload={"edit_rate": rate, "original_body": e["original_body"], "sent_body": body,
                     "original_subject": e["subject"], "sent_subject": subject or e["subject"],
                     "learnings_applied": e.get("learnings_applied") or []})
    log.info("sent email %d to %s (resend id %s, edit rate %.3f)", email_id, s.my_email, msg_id, rate)
    return {"email_id": email_id, "resend_id": msg_id, "to": s.my_email, "subject": subj, "edit_rate": rate}
