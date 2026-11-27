# Real Run captures

The browser game reads `apify_bakeries_25.json` to decide whether it is
showing the bundled rehearsal trace or a sanitized live Apify capture. With no
`recordsPath` in it, as shipped, the records are the bundled rehearsal fixture
`fixtures/apify_bakeries_25.json`.

For a live capture:

1. Run `pipeline.source_bakeries` with `APIFY_TOKEN` in local `.env`.
2. Save the sanitized provider output inside this repository.
3. Add `recordsPath` to `apify_bakeries_25.json` with that file's path relative
   to the repository root, set `live` to `true`, and set `normalised` to `true`
   if the file already contains the script's normalized records.
4. Remove tokens, account IDs, personal information not intended for the
   lesson, and any signed/private URLs before committing.

Never put `APIFY_TOKEN` in a capture. The cinematic masks the token visually,
and the actual credential remains in the untracked local `.env` only.
