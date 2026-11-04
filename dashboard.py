"""Sprinto outreach dashboard.   streamlit run dashboard.py

Tabs: Run summary + metrics | Flagged queue | Drafts (edit, switch hook, approve & send) | Company drill-down.
"""
from __future__ import annotations

import json
import logging

import pandas as pd
import streamlit as st

from pipeline import actions, db
from pipeline.config import get_icp, get_settings
from pipeline.mailer import edit_rate
from pipeline.write import SPRINTO_MD, _corpus, ungrounded_terms

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
st.set_page_config(page_title="Sprinto Outreach", page_icon="📨", layout="wide")
ICP = get_icp()
SETTINGS = get_settings()


# ---------------------------------------------------------------- data

@st.cache_resource(show_spinner=False)
def _conn():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(SETTINGS.database_url, row_factory=dict_row, autocommit=True, connect_timeout=15)


@st.cache_data(ttl=15, show_spinner=False)
def q(sql: str, params: tuple = ()) -> list[dict]:
    """Reads reuse one autocommit connection (Neon round trips dominate page load); reconnect if it dropped."""
    try:
        return _conn().execute(sql, params).fetchall()
    except Exception:  # noqa: BLE001 - stale/closed connection: rebuild once
        _conn.clear()
        return _conn().execute(sql, params).fetchall()


def q1(sql: str, params: tuple = ()) -> dict | None:
    rows = q(sql, params)
    return rows[0] if rows else None


@st.cache_data(ttl=300, show_spinner=False)
def hunter_credits() -> int | None:
    from pipeline.poc import credits_remaining
    return credits_remaining(SETTINGS.hunter_mode == "live")


def refresh(msg: str | None = None) -> None:
    st.cache_data.clear()
    if msg:
        st.session_state["flash"] = msg
    st.rerun()


def link(url: str | None, label: str = "source") -> str:
    return f"[{label}]({url})" if url else "—"


# ---------------------------------------------------------------- sidebar

runs = q("""SELECT id, started_at, status, stop_reason, pool_size, batches_used, researched, qualified, flagged,
                   dropped FROM runs ORDER BY id DESC""")
with st.sidebar:
    st.title("📨 Sprinto Outreach")
    run_id = st.selectbox("Run", [r["id"] for r in runs],
                          format_func=lambda i: next(f"Run {r['id']} · {r['started_at']:%d %b %H:%M} · {r['status']}"
                                                     for r in runs if r["id"] == i)) if runs else None
    st.caption(f"Hunter: **{SETTINGS.hunter_mode}** · searches left: **{hunter_credits()}**")
    st.caption(f"Gemini: `{SETTINGS.gemini_model}` · env: `{SETTINGS.env}`")
    st.caption(f"Emails go to **{SETTINGS.my_email or 'MY_EMAIL not set'}** only")
    if st.button("↻ Refresh data"):
        refresh()

if msg := st.session_state.pop("flash", None):
    st.success(msg)

tab_sum, tab_flag, tab_drafts, tab_drill = st.tabs(["Run summary", "Flagged queue", "Drafts", "Company drill-down"])


# ---------------------------------------------------------------- run summary

with tab_sum:
    if not run_id:
        st.info("No runs yet. Run `python main.py stage1` then `python main.py run`.")
    else:
        r = next(x for x in runs if x["id"] == run_id)
        eligible = q1(f"""SELECT count(*) n FROM companies WHERE status='pooled'
                          AND (seen_at IS NULL OR seen_at < now() - make_interval(days => %s))""",
                      (ICP.seen_cooldown_days,))["n"]
        hunter_spent = q1("""SELECT coalesce(sum(credits_used),0) n FROM api_usage
                             WHERE service='hunter' AND NOT cached AND ok""")["n"]
        c = st.columns(7)
        c[0].metric("Pool at start", r["pool_size"], help=f"{eligible} still unseen now")
        c[1].metric("Batches used", f"{r['batches_used']} / {ICP.max_batches_per_run}")
        c[2].metric("Researched", r["researched"])
        c[3].metric("Qualified", f"{r['qualified']} / {ICP.target_qualified_per_run}")
        c[4].metric("Flagged", r["flagged"])
        c[5].metric("Dropped", r["dropped"])
        c[6].metric("Hunter left", hunter_credits(), help=f"{hunter_spent} live searches spent this period")
        st.caption(f"Stop reason: **{r['stop_reason'] or 'running'}** · started {r['started_at']:%Y-%m-%d %H:%M} UTC")

        st.subheader("Measurement")
        b1 = q1("""SELECT count(*) n,
                          count(*) FILTER (WHERE status IN ('qualified','drafted','sent')
                                           OR flag_reason ILIKE '%%PoC%%' OR flag_reason ILIKE 'email failed%%') q
                   FROM companies WHERE last_run_id=%s AND research_batch=1""", (run_id,))
        fetch = q1("""SELECT avg((evidence->>'_fetches')::int) a, max((evidence->>'_fetches')::int) m
                      FROM companies WHERE last_run_id=%s AND evidence ? '_fetches'""", (run_id,))
        emails = q("""SELECT co.name, k.full_name, e.edit_distance, e.sent_at FROM emails e
                      JOIN companies co ON co.id=e.company_id LEFT JOIN contacts k ON k.id=e.contact_id
                      WHERE e.status='sent' ORDER BY e.sent_at DESC""")
        m = st.columns(5)
        m[0].metric("Batch-1 hit rate", f"{b1['q']}/{ICP.batch_size}",
                    help=f"Qualified in batch 1 ÷ batch size. {b1['q']}/{b1['n']} of the companies batch 1 "
                         f"actually researched before the run stopped.")
        m[1].metric("Flag rate", f"{(r['flagged'] / r['researched'] * 100 if r['researched'] else 0):.0f}%")
        m[2].metric("Fetches / company", f"{(fetch['a'] or 0):.1f}", help=f"max {fetch['m']}, cap "
                                                                         f"{ICP.max_fetches_per_company}")
        m[3].metric("Hunter credits spent", hunter_spent)
        avg_edit = sum(e["edit_distance"] or 0 for e in emails) / len(emails) if emails else None
        m[4].metric("Avg edit rate", f"{avg_edit:.1%}" if avg_edit is not None else "—",
                    help="Normalised edit distance between generated and sent body")

        left, right = st.columns(2)
        with left:
            st.markdown("**Gemini tokens by stage** (this run; non-cached)")
            tok = q("""SELECT stage, count(*) calls, count(*) FILTER (WHERE cached) cached,
                              coalesce(sum(tokens_in) FILTER (WHERE NOT cached),0) tokens_in,
                              coalesce(sum(tokens_out) FILTER (WHERE NOT cached),0) tokens_out
                       FROM api_usage WHERE service='gemini' AND run_id=%s GROUP BY stage ORDER BY stage""",
                    (run_id,))
            st.dataframe(pd.DataFrame(tok), hide_index=True, width="stretch")
            tok_all = q("""SELECT stage, coalesce(sum(tokens_in) FILTER (WHERE NOT cached),0) tokens_in,
                                  coalesce(sum(tokens_out) FILTER (WHERE NOT cached),0) tokens_out
                           FROM api_usage WHERE service='gemini' GROUP BY stage ORDER BY stage""")
            with st.expander("All runs + standalone commands"):
                st.dataframe(pd.DataFrame(tok_all), hide_index=True, width="stretch")
        with right:
            st.markdown("**Drop reasons**")
            drops = q("""SELECT split_part(split_part(drop_reason, ';', 1), ':', 1) AS gate, count(*) n,
                                string_agg(name, ', ') companies
                         FROM companies WHERE last_run_id=%s AND status='dropped' GROUP BY 1 ORDER BY 2 DESC""",
                      (run_id,))
            st.dataframe(pd.DataFrame(drops), hide_index=True, width="stretch")
            st.markdown("**Flag reasons**")
            flags = q("""SELECT split_part(split_part(flag_reason, '(', 1), ':', 1) AS reason, count(*) n,
                                string_agg(name, ', ') companies
                         FROM companies WHERE last_run_id=%s AND (status='flagged' OR needs_review)
                         GROUP BY 1 ORDER BY 2 DESC""", (run_id,))
            st.dataframe(pd.DataFrame(flags), hide_index=True, width="stretch")

        st.markdown("**Companies in this run**")
        comp = q("""SELECT name, domain, funding_stage, round(rank_score::numeric,3) rank, status, needs_review,
                           round(fit_score::numeric,2) fit, (evidence->>'_fetches')::int fetches,
                           coalesce(drop_reason, flag_reason) reason
                    FROM companies WHERE last_run_id=%s ORDER BY updated_at""", (run_id,))
        st.dataframe(pd.DataFrame(comp), hide_index=True, width="stretch")
        if emails:
            st.markdown("**Sent emails: edit rate**")
            st.dataframe(pd.DataFrame(emails), hide_index=True, width="stretch")


# ---------------------------------------------------------------- flagged queue

def gate_table(cid: int, stages: tuple[str, ...] = ("resolve", "qualify", "poc", "human")) -> pd.DataFrame:
    rows = q("""SELECT stage, gate, result, value::text AS value, evidence_quote, source_url, evidence_date, reason,
                       decided_by FROM decisions WHERE company_id=%s AND stage = ANY(%s) ORDER BY id""",
             (cid, list(stages)))
    return pd.DataFrame(rows)


with tab_flag:
    flagged = q("""SELECT * FROM companies WHERE status='flagged'
                   OR (needs_review AND status IN ('qualified','drafted')) ORDER BY status, rank_score DESC""")
    st.caption(f"{len(flagged)} companies need a human decision. Every button below is logged in `decisions` as "
               "decided_by = human.")
    for c in flagged:
        head = f"{'🚩' if c['status'] == 'flagged' else '👀'} {c['name']} · {c['funding_stage'] or 'stage ?'} · " \
               f"{c['domain'] or 'no domain'} · {c['status']}"
        with st.expander(head):
            fund = next((g for g in c["stage1_evidence"] or [] if g.get("gate") == "funding_stage"), {})
            st.markdown(f"**Why it's here:** {c['flag_reason'] or '—'}")
            st.markdown(f"**Unknown after research:** {', '.join(c['unknown_fields']) or 'nothing'}  \n"
                        f"**Funding evidence:** {fund.get('headline', '—')} ({fund.get('date', '')}) "
                        f"{link(fund.get('url'))}")
            gt = gate_table(c["id"])
            if not gt.empty:
                st.dataframe(gt, hide_index=True, width="stretch",
                             column_config={"source_url": st.column_config.LinkColumn("source")})
            k = c["id"]
            a, b, d = st.columns(3)
            with a:
                note = st.text_input("Note", key=f"qn{k}", placeholder="why qualify?")
                label = "Qualify" if c["status"] == "flagged" else "Approve as is"
                if st.button(label, key=f"q{k}", type="primary"):
                    with st.spinner("Qualifying (PoC + draft if needed)…"):
                        msg = actions.qualify(k, note) if c["status"] == "flagged" else actions.approve_review(k, note)
                    refresh(f"{c['name']}: {msg}")
            with b:
                why = st.text_input("Drop reason", key=f"dn{k}", placeholder="e.g. not B2B")
                if st.button("Drop", key=f"d{k}"):
                    refresh(f"{c['name']}: {actions.drop(k, why)}")
            with d:
                dom = st.text_input("Domain", key=f"dom{k}", value=c["domain"] or "", placeholder="acme.io")
                if st.button("Provide domain & research", key=f"r{k}"):
                    with st.spinner(f"Researching {dom} (1–3 min)…"):
                        msg = actions.provide_domain(k, dom)
                    refresh(f"{c['name']}: {msg}")


# ---------------------------------------------------------------- drafts

with tab_drafts:
    drafts = q("""SELECT e.*, co.name company, co.domain, co.needs_review, co.flag_reason, co.funding_stage,
                         k.full_name, k.first_name, k.title, k.email AS poc_email, k.is_mock, k.confidence,
                         k.verification_status, k.pick_reason
                  FROM emails e JOIN companies co ON co.id=e.company_id LEFT JOIN contacts k ON k.id=e.contact_id
                  WHERE e.status IN ('drafted','flagged') ORDER BY co.fit_score DESC NULLS LAST""")
    st.caption(f"{len(drafts)} drafts. Approve & send delivers to **{SETTINGS.my_email or 'MY_EMAIL (not set)'}** "
               "only, never to the prospect.")
    sprinto_text = SPRINTO_MD.read_text(encoding="utf-8")
    for e in drafts:
        tag = "⚠️ validation failed" if e["status"] == "flagged" else ("👀 hook needs review" if e["needs_review"] else "✅")
        with st.expander(f"{e['company']} → {e['full_name']} ({e['title']}) · {tag}", expanded=len(drafts) <= 2):
            info, edit = st.columns([2, 3])
            catalog = e["evidence_catalog"] or []
            by_id = {it["id"]: it for it in catalog}
            with info:
                mock = " · 🧪 **mock contact**" if e["is_mock"] else ""
                st.markdown(f"**PoC:** {e['full_name']}, {e['title']}  \n`{e['poc_email']}` · "
                            f"{e['verification_status']}, {e['confidence']}%{mock}")
                st.caption(e["pick_reason"] or "")
                h = e["hook"] or {}
                st.markdown(f"**Hook ({h.get('evidence_id')}, {h.get('date')}):** {h.get('quote') or ''} "
                            f"{link(h.get('url'))}")
                st.markdown("**Pain points**")
                for p in e["pain_points"] or []:
                    it = by_id.get(p["evidence_id"], {})
                    st.markdown(f"- {p['text']}  \n  ↳ {p['evidence_id']}: _{(it.get('quote') or '')[:120]}_ "
                                f"{link(it.get('url'))}")
                cands = e["hook_candidates"] or []
                if cands:
                    opts = {c["id"]: f"{c['id']} · {c['date']} · {c['fact'][:60]}" for c in cands}
                    pick = st.selectbox("Switch hook", list(opts), format_func=opts.get, key=f"hk{e['id']}",
                                        index=list(opts).index(h.get("evidence_id")) if h.get("evidence_id") in opts else 0)
                    if st.button("Regenerate with this hook", key=f"rg{e['id']}"):
                        with st.spinner("Regenerating…"):
                            msg = actions.choose_hook(e["company_id"], pick)
                        refresh(f"{e['company']}: {msg}")
                with st.popover("Evidence catalog"):
                    st.dataframe(pd.DataFrame(catalog), hide_index=True,
                                 column_config={"url": st.column_config.LinkColumn("url")})
                if e["status"] == "flagged":
                    st.error(f"Validator: {(e['validator_log'] or [{}])[-1].get('errors')}")
            with edit:
                subject = st.text_input("Subject", value=e["subject"] or "", key=f"s{e['id']}")
                body = st.text_area("Body", value=e["original_body"] or "", height=330, key=f"b{e['id']}")
                words = len(body.split())
                company = {"name": e["company"], "domain": e["domain"]}
                contact = {"full_name": e["full_name"], "title": e["title"]}
                nums, names = ungrounded_terms(body, _corpus(catalog, company, contact, sprinto_text,
                                                             SETTINGS.sender_name or "The Sprinto team"))
                st.caption(f"{words} words · edit rate so far {edit_rate(e['original_body'] or '', body):.1%}"
                           + (f" · ⚠️ not in evidence: {nums + names}" if nums or names else " · all terms grounded"))
                if st.button("Approve & send", key=f"send{e['id']}", type="primary",
                             disabled=not body.strip() or not SETTINGS.resend_api_key or not SETTINGS.my_email):
                    from pipeline.mailer import send_email
                    try:
                        res = send_email(e["id"], body=body, subject=subject)
                        refresh(f"Sent to {res['to']} · Resend id {res['resend_id']} · edit rate {res['edit_rate']:.1%}")
                    except Exception as ex:  # noqa: BLE001
                        st.error(str(ex))

    sent = q("""SELECT e.id, co.name company, k.full_name, e.sent_subject, e.sent_to, e.sent_at,
                       round(e.edit_distance::numeric,3) edit_rate, e.provider_message_id
                FROM emails e JOIN companies co ON co.id=e.company_id LEFT JOIN contacts k ON k.id=e.contact_id
                WHERE e.status='sent' ORDER BY e.sent_at DESC""")
    if sent:
        st.subheader("Sent")
        st.dataframe(pd.DataFrame(sent), hide_index=True, width="stretch")


# ---------------------------------------------------------------- drill-down

with tab_drill:
    companies = q("""SELECT id, name, status FROM companies WHERE last_run_id IS NOT NULL OR domain_source='manual'
                     ORDER BY updated_at DESC""")
    if not companies:
        st.info("No researched companies yet.")
    else:
        cid = st.selectbox("Company", [c["id"] for c in companies],
                           format_func=lambda i: next(f"{c['name']} · {c['status']}" for c in companies if c["id"] == i))
        c = q1("SELECT * FROM companies WHERE id=%s", (cid,))
        st.header(f"{c['name']}  ·  {c['status']}")
        cols = st.columns(5)
        cols[0].metric("Stage", c["funding_stage"] or "?")
        cols[1].metric("Rank score", f"{c['rank_score']:.3f}")
        cols[2].metric("Fit score", f"{c['fit_score']:.2f}" if c["fit_score"] is not None else "—")
        cols[3].metric("Fetches", (c["evidence"] or {}).get("_fetches", "—"))
        cols[4].metric("Domain", c["domain"] or "—", help=f"source: {c['domain_source']}")
        if c["drop_reason"] or c["flag_reason"]:
            st.warning(c["drop_reason"] or c["flag_reason"])

        st.subheader("Every gate, with evidence")
        st.dataframe(gate_table(cid, ("stage1", "resolve", "qualify", "poc", "human")), hide_index=True,
                     width="stretch", column_config={"source_url": st.column_config.LinkColumn("source")})

        l, r = st.columns(2)
        with l:
            st.subheader("Rank breakdown (Stage 1)")
            st.json(c["rank_breakdown"] or {}, expanded=False)
            if c["fit_breakdown"]:
                st.subheader("Fit breakdown")
                st.json(c["fit_breakdown"], expanded=False)
        with r:
            st.subheader("Agent loop")
            loop = (c["evidence"] or {}).get("_loop") or []
            if loop:
                st.dataframe(pd.DataFrame([{k: v for k, v in it.items() if k != "gates"} for it in loop]),
                             hide_index=True, width="stretch")
            else:
                st.caption("No refetches.")

        st.subheader("Pages fetched")
        pages = q("""SELECT iteration, target_field, page_type, http_status, text_chars, page_url, fetched_at
                     FROM research WHERE company_id=%s ORDER BY id""", (cid,))
        st.dataframe(pd.DataFrame(pages), hide_index=True, width="stretch",
                     column_config={"page_url": st.column_config.LinkColumn("url")})
        with st.expander("Extracted evidence JSON (verified quotes only)"):
            st.json(c["evidence"] or {})
        rejected = ((c["evidence"] or {}).get("_meta") or {}).get("rejected_facts") or []
        if rejected:
            with st.expander(f"{len(rejected)} fact(s) rejected: quote not found on page"):
                st.json(rejected)

        contact = q1("SELECT * FROM contacts WHERE company_id=%s ORDER BY id DESC LIMIT 1", (cid,))
        if contact:
            st.subheader("Point of contact")
            st.markdown(f"**{contact['full_name']}**, {contact['title']} · `{contact['email']}` · "
                        f"{contact['verification_status']}, {contact['confidence']}%"
                        + (" · 🧪 mock" if contact["is_mock"] else ""))
            st.caption(contact["pick_reason"])
            with st.expander("Full Hunter response"):
                st.json(contact["hunter_raw"])
        for e in q("SELECT * FROM emails WHERE company_id=%s ORDER BY id DESC", (cid,)):
            st.subheader(f"Email · {e['status']}")
            a, b = st.columns(2)
            a.text_area("Generated", e["original_body"] or "", height=260, disabled=True, key=f"o{e['id']}")
            b.text_area("Sent", e["sent_body"] or "(not sent)", height=260, disabled=True, key=f"x{e['id']}")
            if e["edit_distance"] is not None:
                st.caption(f"Edit rate {e['edit_distance']:.1%} · sent {e['sent_at']} to {e['sent_to']}")
