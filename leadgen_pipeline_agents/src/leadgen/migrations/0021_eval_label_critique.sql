-- Error analysis on human labels: what the decision should have been, why it failed, and how.
--
-- A label stays a binary pass/fail ("correct" / "incorrect"). A failed judge decision now also
-- records the verdict the reviewer would have given, a one-line critique, and failure-mode tags
-- from the category's list (config/category.yaml), so labels can be counted by failure mode and
-- compared with the judge class by class. Rows written before this migration keep working: their
-- correct_verdict is NULL, their failure_modes empty and their critique blank.
ALTER TABLE eval_labels
    -- qualified / rejected / review: what the decision should have been. On a pass it equals the
    -- recorded verdict; NULL on labels saved before this migration and on non-judge records.
    ADD COLUMN correct_verdict text
        CHECK (correct_verdict IS NULL OR correct_verdict IN ('qualified', 'rejected', 'review')),
    -- Failure-mode tags, e.g. {"no CFP® on the page", "wrong market"}.
    ADD COLUMN failure_modes   text[] NOT NULL DEFAULT '{}',
    -- One line on why the decision was wrong (required by the review page on a fail).
    ADD COLUMN critique        text   NOT NULL DEFAULT '';
