-- Stage 6: add the transient 'publishing' status used by the cron autoposter.
-- The autoposter atomically claims a due draft by moving it scheduled -> publishing
-- (UPDATE ... WHERE status='scheduled'), which guarantees a repeat/concurrent run
-- cannot pick the same row (idempotency). It then settles to published/failed.

ALTER TABLE hermes_agent.content_drafts
    DROP CONSTRAINT IF EXISTS content_drafts_status_check;

ALTER TABLE hermes_agent.content_drafts
    ADD CONSTRAINT content_drafts_status_check CHECK (
        status IN (
            'draft', 'previewed', 'approved', 'scheduled',
            'publishing', 'published', 'failed', 'cancelled'
        )
    );
