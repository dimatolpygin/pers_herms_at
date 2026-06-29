-- Stage 4: content drafts for Hermes Agent.
-- This project uses a dedicated schema because the local Postgres instance is
-- shared with other projects.

CREATE SCHEMA IF NOT EXISTS hermes_agent;

CREATE TABLE IF NOT EXISTS hermes_agent.content_drafts (
    id BIGSERIAL PRIMARY KEY,
    source_type TEXT NOT NULL DEFAULT 'freeform',
    source_value TEXT,
    topic TEXT NOT NULL,
    product_name TEXT,
    raw_input JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'draft',
    text_versions JSONB NOT NULL,
    selected_version INTEGER NOT NULL DEFAULT 1,
    image_prompt TEXT,
    image_task_id TEXT,
    image_url TEXT,
    image_path TEXT,
    platforms JSONB NOT NULL DEFAULT '[]'::jsonb,
    scheduled_at TIMESTAMPTZ,
    publication_links JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT content_drafts_status_check CHECK (
        status IN ('draft', 'previewed', 'approved', 'scheduled', 'published', 'failed', 'cancelled')
    ),
    CONSTRAINT content_drafts_selected_version_check CHECK (selected_version >= 1),
    CONSTRAINT content_drafts_text_versions_array_check CHECK (jsonb_typeof(text_versions) = 'array'),
    CONSTRAINT content_drafts_text_versions_min_check CHECK (jsonb_array_length(text_versions) >= 3),
    CONSTRAINT content_drafts_platforms_array_check CHECK (jsonb_typeof(platforms) = 'array')
);

CREATE INDEX IF NOT EXISTS idx_content_drafts_status
    ON hermes_agent.content_drafts (status);

CREATE INDEX IF NOT EXISTS idx_content_drafts_scheduled_at
    ON hermes_agent.content_drafts (scheduled_at)
    WHERE scheduled_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_content_drafts_created_at
    ON hermes_agent.content_drafts (created_at DESC);

CREATE OR REPLACE FUNCTION hermes_agent.touch_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_content_drafts_touch_updated_at ON hermes_agent.content_drafts;

CREATE TRIGGER trg_content_drafts_touch_updated_at
BEFORE UPDATE ON hermes_agent.content_drafts
FOR EACH ROW
EXECUTE FUNCTION hermes_agent.touch_updated_at();
