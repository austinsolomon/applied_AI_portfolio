# Migrations

Schema changes, applied in order by `leadgen db migrate`.

- Name files `NNNN_description.sql` (four-digit version, lowercase words joined by `_`).
- Each file runs once, in one transaction, and is recorded in `schema_migrations`.
- Never edit a file after it has been merged: the runner stores each file's checksum and refuses to
  run if an applied file changed. Add a new file instead.
