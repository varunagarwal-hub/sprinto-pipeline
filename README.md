# Sprinto cold outreach pipeline

This pipeline finds recently funded SaaS companies, works out which ones need Sprinto, picks the right person to contact, and drafts a personalised cold email. A human approves each email before it is sent. Every decision (why this company, why this person, why this email) can be traced to evidence stored in Neon: a verbatim quote, its source URL and a date.

```
Google News RSS ──► Stage 1: regex gates + rank (no LLM, no scraping) ──► ranked pool in Neon
                                                                              │
             ┌──────────────── batch runner: next 20 unseen by rank ◄─────────┘
             ▼
  resolve domain ─► crawl (homepage + 2 pages) ─► Gemini extract (quotes verified in code)
             ─► Python gates ─► agent loop: fetch the page most likely to answer what's unknown (≤3)
             ─► dropped │ flagged │ qualified(+review)
                                      │
                    Hunter PoC (1 search) ─► Gemini writer ─► code validator ─► draft in Neon
                                                                                     │
  Streamlit dashboard: clear flags, switch hook, edit, Approve & send ─► Resend ─► MY_EMAIL only
```

Gemini only **extracts** facts and **writes** the email. Python rules make every decision.

## Setup (Windows, Python 3.11+)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
cp .env.example .env        # then fill it in
.venv/Scripts/python main.py migrate
.venv/Scripts/python main.py check
```

The `.env` file holds:

| Key | Purpose |
|---|---|
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Gemini (`google-genai`). Defaults to `gemini-3.8-flash`, the newest stable Flash model on 2026-11-04 |
| `HUNTER_API_KEY`, `HUNTER_MODE` | `mock` (the default) reads `fixtures/hunter_mock.json` and spends nothing. `live` makes real searches |
| `DATABASE_URL` | Neon Postgres |
| `RESEND_API_KEY`, `RESEND_FROM` | Resend email. `onboarding@resend.dev` works without a verified domain |
| `MY_EMAIL` | **The only recipient.** Without a verified Resend domain, this must be your Resend account email |
| `SENDER_NAME` | Sign-off name in the emails |
| `PIPELINE_ENV` | `dev` caches every HTTP page and Gemini response under `.cache/` |

## Running it

```bash
.venv/Scripts/python main.py stage1                    # news -> gates -> ranked pool; prints the top 20
.venv/Scripts/python main.py --domains domains.txt     # add manual domains at the top of the pool
.venv/Scripts/python main.py run                       # batches of 20 until 5 qualified (max 3 batches)
.venv/Scripts/python -m streamlit run dashboard.py     # review, edit, approve & send
```

Single-step commands, useful for demos and debugging:

| Command | What it does |
|---|---|
| `main.py extract <domain>` | Crawl one site and print the verified evidence JSON |
| `main.py poc` | Find a PoC for qualified companies that don't have one yet |
| `main.py write [company_id]` | Write or regenerate drafts |
| `main.py send <email_id>` | Send one draft to `MY_EMAIL` |
| `main.py pool` | Print the top 20 of the pool |

Tests: `.venv/Scripts/python -m pytest -q` runs 98 tests covering the Stage 1 regexes and gates, quote verification, qualifier rules, PoC picking and the email validator.

## How each decision is made

All thresholds live in [`icp.json`](icp.json), so the ICP can change without code edits. Every run stores a snapshot of `icp.json`.

### Stage 1: shortlist ([`pipeline/stage1.py`](pipeline/stage1.py))
- **Source:** 12 funding queries × 2 regions (India and global) on Google News RSS, limited to the last 2 years.
- **Company name:** extracted from the headline by regex. Descriptors are stripped, so "Logistics startup OORJAA raises…" gives OORJAA and "Norway's Unleash secures…" gives Unleash. Headlines the regex can't parse are dropped and logged.
- **Gates, in order:**
  1. Series A, B or C in the headline (not pre-Series and not a seed round).
  2. Article no older than 24 months.
  3. Name parsed.
  4. No exclude keyword.
  A SaaS keyword is a bonus, never a gate.
- **Rank** = 0.45·recency + 0.35·industry weight + boost keywords (capped at 0.15) + 0.05 if a SaaS keyword is present.
- **Dedupe and cooldown:** companies are deduplicated on a normalised name. Anything researched in the last 90 days is skipped.
- **Evidence:** each gate's headline, URL and date go to `companies.stage1_evidence` and `decisions`. Every dropped headline goes to `stage1_runs`.

### Domain ([`pipeline/resolve.py`](pipeline/resolve.py))
The resolver tries `name.com/.io/.ai/.co/.in`. It accepts a domain only if **both** of these hold:
- the homepage's title, `og:site_name` or logo alt text names the company;
- the homepage shares a topic word with the funding headline.

This rejects same-name companies (moment.com is not the payments company Moment) and parked domains. **If neither check passes, the company is flagged with "domain unresolved" and nothing is guessed.** You can then use "Provide domain" on the dashboard.

### Research ([`pipeline/scraper.py`](pipeline/scraper.py), [`pipeline/extract.py`](pipeline/extract.py))
- **Crawl budget:** at most 6 fetches per company. The first crawl uses 3 (homepage, trust page, careers page), leaving 3 for the agent loop.
- **Job boards:** Greenhouse, Lever and Ashby are read through their public JSON APIs, which give each posting's date.
- **Trust-center vendor:** detected **in the raw HTML** (for example `assets.vanta.com`). These trust pages load with JavaScript, so their visible text is often empty.
- **Extraction:** Gemini returns strict JSON. Every fact has a `value`, a verbatim `evidence_quote`, a `source_url` and a `date`, or is null. **Python then checks that each quote really appears on the cited page.** A fact whose quote can't be found is set to null and listed under `rejected_facts`.

### Qualifier ([`pipeline/qualify.py`](pipeline/qualify.py))

| Gate | Fail = drop | Unknown when |
|---|---|---|
| `is_saas` | The site describes a services, hardware or agency business | No statement either way |
| `funding_stage` | Not Series A–C | No stage on record (a manual domain with no stage given) |
| `certifications` | Already holds SOC 2 **and** ISO 27001 **and** GDPR | No trust or security page read |
| `competitor_trust_vendor` | Trust center runs on Vanta or Drata, **or already on Sprinto** (an existing customer) | No trust or security page read |
| `employee_count` | More than 1,000 | Not stated |
| `audit_signal` | (not a drop gate) | No signal of these kinds dated within 90 days: GRC, security or compliance hire; certification in progress; enterprise customer; expansion into regulated markets |

- **Signal checks:** job-post signals must match `audit_role_keywords` in code. Signals that are undated or older than 90 days count as unknown.
- **Agent loop:** while a gate is unknown, the loop fetches the next page type from `refetch_map` (for example, careers for an audit signal, or trust and security pages for certifications), re-extracts, merges and re-checks. It stops after 3 refetches. A known fact is never overwritten by a null.
- **Outcome:**
  - Any gate fails → **dropped**.
  - SaaS or funding stage still unknown → **flagged**, and no Hunter credit is spent.
  - Only the audit signal unknown → **qualified + review**.
  - Everything passes → **qualified**.
- **Fit score** is a weighted sum of: employees between 50 and 200, enterprise customers, a sensitive industry, a known cloud provider, and holding some but not all target certifications (an upsell).
- **Run stop:** the run ends as soon as it has 5 qualified companies.

### Point of contact ([`pipeline/poc.py`](pipeline/poc.py))
- **One Hunter Domain Search per company.** Each live response is saved to `.cache/hunter/<domain>.json` the moment it arrives, then to Neon, and is reused from then on, never bought again. In live mode, the free `/account` check runs first, and the search is refused if fewer than 1 search remains. Contacts are append-only: the newest row is the current PoC.
- **Picking rules by stage:**
  - **Series A/B:** CTO, then VP Engineering, then co-founder.
  - **Series C:** CISO, then Head of Security, then Head of Compliance, then CTO.
- **Order of preference:** stage rule first, then email verification status, then Hunter confidence. Generic mailboxes, invalid emails and anything under 70% confidence are skipped. Every email considered is stored with why it was or wasn't picked.

### Email ([`pipeline/write.py`](pipeline/write.py), [`pipeline/mailer.py`](pipeline/mailer.py))
- **Evidence and hook:** Python builds a numbered evidence list and **picks the hook**. The order is: a dated audit signal from the last 90 days, then the funding round, then any other dated item. A hook chosen by a human always wins.
- **Writing:** one Gemini call returns 2 pain points, the hook, the subject, the body and a claims list. Each item cites an evidence ID.
- **The validator (code) checks:**
  - the cited IDs exist and the hook is the one chosen;
  - the email greets the person by first name and is under 120 words;
  - it has exactly one ask (a 15-minute call) and is plain text;
  - the opening refers to the hook;
  - **every number and proper noun appears in the evidence or in [`sprinto.md`](sprinto.md).**
  If any check fails, the draft is regenerated once with the errors as feedback. A second failure flags it.
- **Sending:** the sender has **no `to` parameter**, so mail can only go to `MY_EMAIL`. The subject is prefixed `[DRAFT for <PoC> @ <company>]`. Neon stores both the generated and the sent body, plus the edit rate (normalised Levenshtein distance).

## Human gates (dashboard)
1. **Flagged queue:** Qualify, Approve as is, Drop (with a reason), or Provide domain & research, which reruns the full research for that one company.
2. **Drafts:** see the PoC and why they were picked, plus the hook and pain points with source links. You can switch the hook and regenerate. You can edit the subject and body, with a live check that every term is still in the evidence. **Approve & send** sends your edited version.

Every human action is written to `decisions` with `decided_by = 'human'`. The **Company drill-down** tab shows every gate with its quote, URL and date, the agent loop's steps, the pages fetched, the raw evidence, the Hunter response, and the generated and sent emails side by side.

## Measurement
For each run, the Run summary tab shows:
- Gemini tokens per stage (non-cached)
- fetches per company
- the batch-1 hit rate: qualified ÷ 20, plus qualified ÷ companies actually researched
- the flag rate
- Hunter credits spent and remaining
- the edit rate for each sent email

## Database (Neon)
| Table | What it holds |
|---|---|
| `runs` | One row per batch run, including the `icp_snapshot` |
| `stage1_runs` | One row per Stage 1 sweep, with every dropped headline |
| `companies` | The pool: Stage 1 evidence, rank, status, merged evidence, fit |
| `research` | Every page fetched, with its iteration, target field and extraction |
| `decisions` | Every gate (rule or human) with its quote, URL, date and reason |
| `contacts` | Chosen PoC, the full Hunter response, and a mock flag |
| `emails` | Evidence list, hook options, generated and sent body, edit rate, Resend ID |
| `api_usage` | Every Gemini, Hunter, Google News and Resend call: tokens, credits, cached or not |

Migrations are plain SQL in `migrations/`, applied by `main.py migrate`.

## Changes from the original spec
- **Resend instead of Gmail.** Drafts live in Neon and are edited in the dashboard rather than in Gmail drafts. The edit rate still compares the generated body with the sent one.
- **Every qualified company gets a draft**, using the funding round as a default dated hook when there's no fresh audit signal. "Needs review" companies are marked, and you can switch the hook before approving.
- **Gemini model:** `gemini-3.8-flash`, not 2.5.

## Known limitations
- Domain resolution only tries `name + TLD`, so sites like `y.uno` (Yuno) need "Provide domain".
- Company websites rarely date their signals. Most dated signals come from job boards, so many qualified companies need the human hook review.
- Funding evidence links point to Google News redirect URLs, not the publisher's page.
- The agent loop's last spare fetch often goes on headcount, which sites rarely state.
