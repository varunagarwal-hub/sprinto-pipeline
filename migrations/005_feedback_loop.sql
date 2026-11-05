-- 005: feedback loop. Act (drafts/sends) and Signal (reviewer + prospect reactions) already existed;
-- this adds Capture (feedback_events), Store (learnings), Retrieve/Apply traceability (learning_applications).

ALTER TABLE companies ADD COLUMN article_url text;        -- publisher URL decoded from the Google News link
ALTER TABLE companies ADD COLUMN queued_action text;      -- 'research' | 'regenerate' | 'poc_write' (set by the web dashboard)
ALTER TABLE emails ADD COLUMN learnings_applied bigint[] NOT NULL DEFAULT '{}';
ALTER TABLE emails ADD COLUMN outcome text CHECK (outcome IN ('replied', 'meeting', 'not_interested', 'no_reply', 'bounced'));
ALTER TABLE emails ADD COLUMN outcome_at timestamptz;
ALTER TABLE emails ADD COLUMN rating smallint CHECK (rating IN (-1, 1));
ALTER TABLE emails DROP CONSTRAINT emails_status_check;
ALTER TABLE emails ADD CONSTRAINT emails_status_check
    CHECK (status IN ('drafted', 'flagged', 'sent', 'discarded', 'superseded'));

-- CAPTURE: every signal, raw, with the context needed to generalise from it later.
CREATE TABLE feedback_events (
    id            bigserial PRIMARY KEY,
    created_at    timestamptz NOT NULL DEFAULT now(),
    company_id    bigint REFERENCES companies(id) ON DELETE CASCADE,
    email_id      bigint REFERENCES emails(id) ON DELETE SET NULL,
    source        text NOT NULL CHECK (source IN ('reviewer', 'prospect', 'webhook', 'system')),
    kind          text NOT NULL,      -- draft_sent | draft_rating | hook_switch | override_qualify | override_drop
                                      -- | review_approved | outcome | delivery
    rating        smallint,           -- -1 / +1 where it applies
    tags          text[] NOT NULL DEFAULT '{}',
    note          text,
    payload       jsonb NOT NULL DEFAULT '{}',
    context       jsonb NOT NULL DEFAULT '{}',   -- stage, industry tags, PoC role, hook field at capture time
    processed_at  timestamptz                    -- set when distilled into learnings
);
CREATE INDEX feedback_events_kind_idx ON feedback_events (kind, created_at DESC);
CREATE INDEX feedback_events_unprocessed_idx ON feedback_events (processed_at) WHERE processed_at IS NULL;

-- STORE: distilled, retrievable memory. One row per lesson; support grows as more events agree.
CREATE TABLE learnings (
    id                  bigserial PRIMARY KEY,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    scope               text NOT NULL CHECK (scope IN ('writer', 'hook', 'ranking', 'qualifier')),
    kind                text NOT NULL,   -- avoid_phrase | prefer_phrase | style | hook_preference | industry_adjust
    key                 text NOT NULL,   -- dedupe key, e.g. 'avoid:audit fire drills'
    lesson              text NOT NULL,   -- human-readable instruction
    applies_to          jsonb NOT NULL DEFAULT '{}',   -- {"role": "...", "industry": "...", "stage": "..."}; {} = global
    value               double precision,              -- numeric effect where relevant (rank delta, hook score)
    support             int NOT NULL DEFAULT 1,
    confidence          double precision NOT NULL DEFAULT 0.5,
    evidence_event_ids  bigint[] NOT NULL DEFAULT '{}',
    status              text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    times_applied       int NOT NULL DEFAULT 0,
    last_applied_at     timestamptz,
    UNIQUE (scope, key)
);

-- RETRIEVE + APPLY: which learnings were used where.
CREATE TABLE learning_applications (
    id           bigserial PRIMARY KEY,
    learning_id  bigint NOT NULL REFERENCES learnings(id) ON DELETE CASCADE,
    company_id   bigint REFERENCES companies(id) ON DELETE CASCADE,
    email_id     bigint REFERENCES emails(id) ON DELETE SET NULL,
    scope        text NOT NULL,
    applied_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX learning_applications_learning_idx ON learning_applications (learning_id);
