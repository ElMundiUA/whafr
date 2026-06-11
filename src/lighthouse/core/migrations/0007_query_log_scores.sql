-- 0007_query_log_scores — usefulness signals for gap detection.
--
-- Vector search almost never returns *zero* hits, so the 0006-era
-- gap definition (hit_count = 0) under-detects coverage gaps. Two new
-- nullable columns let the gap classifier do better:
--
--   top_score    — the best hit's fused retrieval score (telemetry;
--                  scale varies with reranker settings, not used for
--                  the gap decision itself).
--   useful_score — avg 1-5 usefulness of the returned hits as rated
--                  by the LLM classifier (see core/usefulness.py).
--                  NULL when the classifier is disabled. A non-NULL
--                  value below the useful-threshold marks the search
--                  as a gap even though hits came back.

ALTER TABLE query_log ADD COLUMN IF NOT EXISTS top_score    REAL;
ALTER TABLE query_log ADD COLUMN IF NOT EXISTS useful_score REAL;
