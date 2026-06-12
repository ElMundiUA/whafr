-- 0011_run_queue — durable importer-run queue.
--
-- Runs used to live only in asyncio tasks: a pod restart mid-crawl
-- lost the run and the boot sweep could only mark it 'cancelled' —
-- nobody retried. Runs are now enqueued as rows (status 'queued') and
-- executed by a worker that claims them with FOR UPDATE SKIP LOCKED,
-- which also makes multi-replica execution safe (each run claimed by
-- exactly one worker).
--
-- `requeues` is the crash-retry budget: the boot sweep re-queues a
-- 'running' orphan once; a second crash on the same run cancels it,
-- so a run that reliably kills its pod can't crash-loop forever.

ALTER TABLE importer_runs DROP CONSTRAINT IF EXISTS importer_runs_status_check;
ALTER TABLE importer_runs ADD CONSTRAINT importer_runs_status_check
    CHECK (status IN ('queued', 'running', 'success', 'error', 'cancelled'));

ALTER TABLE importer_runs ADD COLUMN IF NOT EXISTS requeues INT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS importer_runs_queued_idx
    ON importer_runs (started_at)
    WHERE status = 'queued';
