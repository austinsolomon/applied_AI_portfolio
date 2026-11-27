# Use the project skills

Your copy already includes four real directories under `.claude/skills/`:
`source-leads`, `enrich-leads`, `qualify-leads` and `outreach`. They work the same
whether you downloaded the ZIP from the course or used the personal copy command
on your course account page; no symlink or global installation is needed.

Open Claude Code inside this repository. Enter `/source-leads`, then run the other
three in pipeline order, checking the saved output each time. Each skill runs the
same Python script with `--offline`, so a saved provider key cannot turn the
practice run into live sourcing or sending.

Claude Code uses your own Claude access and may incur model usage. For the free
local path, run the Python commands directly instead:

```bash
python3 -m pipeline.source_bakeries --offline
python3 -m pipeline.enrich --offline
python3 -m pipeline.qualify --offline
python3 -m pipeline.outreach --offline
```

Readable copies in `skills/` supply the game's code preview. A test keeps those
copies equal to the discoverable project skills. If you edit one, update its
matching copy too. Start at the repository README for the complete setup.

## Getting updates

If you used the copy command, open your course account page, get a fresh command
for this repository and run the one under **Update my copy**, from the folder
that holds `leadgen-skills-pipeline`. A plain `git pull` will not work, because
your copy carries no access token. If you started from the ZIP, download the ZIP
again.
