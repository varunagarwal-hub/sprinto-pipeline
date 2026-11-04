-- 001_init: core schema for the Sprinto outreach pipeline.
-- Every decision must be traceable: companies -> research -> decisions -> contacts -> emails.

CREATE TABLE runs (
    id            bigserial PRIMARY KEY,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        text NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running', 'completed', 'failed')),
    stop_reason   text,                       -- target_reached | max_batches | pool_empty | error
    icp_snapshot  jsonb NOT NULL,             -- icp.json as it was for this run
    pool_size     int NOT NULL DEFAULT 0,
    batches_used  int NOT NULL DEFAULT 0,
    researched    int NOT NULL DEFAULT 0,
    qualified     int NOT NULL DEFAULT 0,
    flagged       int NOT NULL DEFAULT 0,
    dropped       int NOT NULL DEFAULT 0,
    notes         text
);

CREATE TABLE companies (
    id               bigserial PRIMARY KEY,
    name             text NOT NULL,
    norm_name        text NOT NULL UNIQUE,
    domain           text,
    domain_source    text CHECK (domain_source IN ('resolved', 'manual', 'human')),
    source           text NOT NULL DEFAULT 'news' CHECK (source IN ('news', 'manual')),
    funding_stage    text,                    -- 'Series A' | 'Series B' | 'Series C' | NULL
    funding_date     date,
    funding_amount   text,
    industry_tags    text[] NOT NULL DEFAULT '{}',
    rank_score       double precision NOT NULL DEFAULT 0,
    rank_breakdown   jsonb NOT NULL DEFAULT '{}',
    stage1_evidence  jsonb NOT NULL DEFAULT '[]',   -- [{gate, headline, url, date, publisher, matched}]
    evidence         jsonb,                   -- merged extractor output used by qualifier + writer
    status           text NOT NULL DEFAULT 'pooled'
                     CHECK (status IN ('pooled', 'researching', 'qualified', 'flagged',
                                       'dropped', 'drafted', 'sent')),
    needs_review     boolean NOT NULL DEFAULT false, -- true for "qualify and flag"
    flag_reason      text,
    drop_reason      text,
    unknown_fields   text[] NOT NULL DEFAULT '{}',
    fit_score        double precision,
    fit_breakdown    jsonb,
    chosen_hook      jsonb,                   -- set by a human when audit signal was unknown
    seen_at          timestamptz,             -- set when Stage 2 research starts
    last_run_id      bigint REFERENCES runs(id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX companies_pool_idx ON companies (status, rank_score DESC);
CREATE INDEX companies_seen_idx ON companies (seen_at);

CREATE TABLE research (
    id            bigserial PRIMARY KEY,
    company_id    bigint NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    run_id        bigint REFERENCES runs(id),
    iteration     int NOT NULL DEFAULT 0,    -- 0 = initial crawl, 1..N = agent-loop refetches
    target_field  text,                      -- which unknown field the refetch was chasing
    page_url      text NOT NULL,
    page_type     text,                      -- homepage | about | careers | ats | security | trust | pricing | customers
    http_status   int,
    text_chars    int,
    text_hash     text,
    page_text     text,
    extracted     jsonb,
    fetched_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX research_company_idx ON research (company_id, iteration);

CREATE TABLE decisions (
    id              bigserial PRIMARY KEY,
    company_id      bigint NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    run_id          bigint REFERENCES runs(id),
    stage           text NOT NULL CHECK (stage IN ('stage1', 'resolve', 'qualify', 'poc', 'write', 'human')),
    gate            text NOT NULL,
    result          text NOT NULL CHECK (result IN ('pass', 'fail', 'unknown', 'override')),
    value           jsonb,
    evidence_quote  text,
    source_url      text,
    evidence_date   text,                    -- free text; source dates are often partial
    reason          text,
    decided_by      text NOT NULL DEFAULT 'rule' CHECK (decided_by IN ('rule', 'human')),
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX decisions_company_idx ON decisions (company_id, created_at);

CREATE TABLE contacts (
    id                   bigserial PRIMARY KEY,
    company_id           bigint NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    run_id               bigint REFERENCES runs(id),
    full_name            text,
    first_name           text,
    title                text,
    email                text,
    confidence           int,
    verification_status  text,
    seniority            text,
    department           text,
    pick_rule            text,               -- which poc_rules entry matched
    pick_reason          text,
    hunter_raw           jsonb,              -- full Hunter response, kept for audit
    is_mock              boolean NOT NULL DEFAULT false,
    created_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE emails (
    id                bigserial PRIMARY KEY,
    company_id        bigint NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    contact_id        bigint REFERENCES contacts(id),
    run_id            bigint REFERENCES runs(id),
    pain_points       jsonb,
    hook              jsonb,
    claims            jsonb,
    subject           text,
    original_body     text,
    sent_body         text,
    edit_distance     double precision,      -- normalised 0..1 between original and sent body
    validator_log     jsonb,
    attempts          int NOT NULL DEFAULT 1,
    gmail_draft_id    text,
    gmail_message_id  text,
    status            text NOT NULL DEFAULT 'drafted'
                      CHECK (status IN ('drafted', 'flagged', 'sent', 'discarded')),
    created_at        timestamptz NOT NULL DEFAULT now(),
    sent_at           timestamptz
);

CREATE TABLE api_usage (
    id                 bigserial PRIMARY KEY,
    run_id             bigint REFERENCES runs(id),
    company_id         bigint REFERENCES companies(id) ON DELETE SET NULL,
    service            text NOT NULL CHECK (service IN ('gemini', 'hunter', 'google_news', 'http', 'gmail')),
    stage              text,                 -- stage1 | resolve | scrape | extract | poc | write | send
    model              text,
    tokens_in          int,
    tokens_out         int,
    credits_used       int,
    credits_remaining  int,
    cached             boolean NOT NULL DEFAULT false,
    ok                 boolean NOT NULL DEFAULT true,
    error              text,
    ts                 timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX api_usage_run_idx ON api_usage (run_id, service);
