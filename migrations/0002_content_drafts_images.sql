-- Stage 4: a post can carry several images (e.g. a generated marketplace card
-- plus the raw product photo). Store the ordered gallery alongside the primary
-- image_url/image_path (which stays = the main/card image for compatibility).

ALTER TABLE hermes_agent.content_drafts
    ADD COLUMN IF NOT EXISTS images JSONB NOT NULL DEFAULT '[]'::jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'content_drafts_images_array_check'
    ) THEN
        ALTER TABLE hermes_agent.content_drafts
            ADD CONSTRAINT content_drafts_images_array_check CHECK (jsonb_typeof(images) = 'array');
    END IF;
END $$;
