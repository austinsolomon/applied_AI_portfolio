CREATE TABLE agent_columns (
    column_name text PRIMARY KEY,
    config jsonb NOT NULL
);
INSERT INTO agent_columns VALUES ('qualification', '{
    "pages": ["", "services", "about"], "searches": 1,
    "run_if": "not_duplicate", "max_evidence_characters": 18000,
    "prompt_template": "Qualify {{business_name}} for {{market}} using only cited evidence."
}');
