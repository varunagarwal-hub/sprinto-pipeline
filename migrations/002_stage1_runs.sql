-- 002: one row per Stage 1 sweep, so pool numbers and every dropped headline stay auditable.
CREATE TABLE stage1_runs (
    id                bigserial PRIMARY KEY,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    queries           int NOT NULL DEFAULT 0,
    items_fetched     int NOT NULL DEFAULT 0,
    items_unique      int NOT NULL DEFAULT 0,
    companies_kept    int NOT NULL DEFAULT 0,
    pooled_new        int NOT NULL DEFAULT 0,
    pooled_updated    int NOT NULL DEFAULT 0,
    skipped_seen      int NOT NULL DEFAULT 0,
    drop_counts       jsonb NOT NULL DEFAULT '{}',   -- {reason: count}
    drops             jsonb NOT NULL DEFAULT '[]'    -- [{headline, url, date, reason}]
);
