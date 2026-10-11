-- Eval capture and human review.
--
-- eval_records is where the pipeline writes every automated decision it makes: a sample grader's
-- verdict on a scrape cell, the judge's decision on one lead, a contradiction the CRM write found.
-- It is created here IF NOT EXISTS because the sourcing, qualification and CRM-write work define
-- the same table from their side; whichever migration runs first wins and the other is a no-op.
CREATE TABLE IF NOT EXISTS eval_records (
    eval_record_id bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- sample_verdict: grader on a sampled scrape cell · judge_decision: qualification of one lead
    -- contradiction: a qualified lead whose reason affirms a disqualifier
    kind           text        NOT NULL
        CHECK (kind IN ('sample_verdict', 'judge_decision', 'contradiction')),
    -- What was decided about: a cell label ("Phoenix, AZ · massage therapist") or a business.
    subject        text        NOT NULL CHECK (subject <> ''),
    -- PROCEED / RECONFIGURE / ABORT for a sample; qualified / rejected / review for a judge
    -- decision; contradiction for a contradiction hit.
    verdict        text        NOT NULL CHECK (verdict <> ''),
    reason         text        NOT NULL DEFAULT '',
    -- The inputs and structured output behind the verdict, enough to replay it in a test.
    evidence       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    model          text,
    run_id         uuid,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS eval_records_kind_created_idx ON eval_records (kind, created_at DESC);

-- One human label per record. Relabeling replaces the earlier label; the record itself is never
-- changed, so an exported fixture always shows what the pipeline actually decided.
CREATE TABLE eval_labels (
    eval_record_id bigint      PRIMARY KEY REFERENCES eval_records ON DELETE CASCADE,
    label          text        NOT NULL CHECK (label IN ('correct', 'incorrect')),
    note           text        NOT NULL DEFAULT '',
    labeled_by     text        NOT NULL CHECK (labeled_by <> ''),
    labeled_at     timestamptz NOT NULL DEFAULT now()
);
