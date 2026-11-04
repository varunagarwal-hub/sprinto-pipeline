-- 004: record which batch of a run researched each company (for the batch-1 hit rate metric).
ALTER TABLE companies ADD COLUMN research_batch int;
UPDATE companies SET research_batch = 1 WHERE last_run_id IS NOT NULL AND research_batch IS NULL;
