-- 003: email delivery moves from Gmail drafts to Resend. Drafts live in Neon and are edited in the dashboard.
ALTER TABLE emails DROP COLUMN gmail_draft_id;
ALTER TABLE emails RENAME COLUMN gmail_message_id TO provider_message_id;
ALTER TABLE emails ADD COLUMN provider text NOT NULL DEFAULT 'resend';
ALTER TABLE emails ADD COLUMN sent_subject text;
ALTER TABLE emails ADD COLUMN sent_to text;                -- always MY_EMAIL, recorded for audit
ALTER TABLE emails ADD COLUMN evidence_catalog jsonb;      -- numbered evidence items the writer was given
ALTER TABLE emails ADD COLUMN hook_candidates jsonb;       -- dated items a human can switch the hook to
ALTER TABLE api_usage DROP CONSTRAINT api_usage_service_check;
ALTER TABLE api_usage ADD CONSTRAINT api_usage_service_check
    CHECK (service IN ('gemini', 'hunter', 'google_news', 'http', 'gmail', 'resend'));
