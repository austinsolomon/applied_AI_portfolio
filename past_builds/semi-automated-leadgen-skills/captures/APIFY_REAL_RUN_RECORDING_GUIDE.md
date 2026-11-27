# Apify Real Run — Recording Guide

This guide covers the complete recording process for the passive Apify Real Run
shown after the Apify Trawler replay.

The demonstration connects four ideas:

1. The carrier pigeon represented an API key.
2. The trawler represented an asynchronous Apify actor.
3. The harbour replay showed the sourcing system from above.
4. The Real Run shows the same system operating through Claude Code, Python,
   Apify, and a real lead artifact built from 25 Google Maps rows.

The player will only watch the finished demonstration. They will not type,
approve actions, or provide credentials.

## What happens after recording

The submitted video will be processed and inserted into the browser game. The
integration can include:

- Trimming and accelerating waiting periods
- Cropping and masking sensitive areas
- Captions, arrows, labels, and code highlights
- Browser-optimized H.264 and WebM versions
- A poster frame for loading
- Responsive playback and captions
- A skip control and reduced-motion behavior
- Saving the lead handoff after playback
- Returning the player to the Glade and unlocking Guide Lecture 2

No image generation or AI video generation is required. A real screen recording
is the source of truth.

## Required recording format

- Resolution: **1920 × 1080**
- Frame rate: **30 FPS**
- Color: **SDR / Rec.709**; do not record in HDR
- Container: **MP4**
- Orientation: landscape
- Webcam: off
- Microphone: off
- System audio: optional; silence is preferred because captions will be added
- Browser zoom: 100%
- Terminal font: approximately 18–20 px

Submit the original, unedited recording. Long waits and mistakes can be removed
during editing.

## Security rules

The full Apify token must never be visible in the recording or submitted files.

- Show only the `apify_api_` prefix and a masked remainder.
- Do not run `cat .env`.
- Do not open `.env` in an editor while recording.
- Do not submit `.env`.
- Close tabs containing billing information, personal email, or unrelated
  account data.
- If the token is accidentally exposed, revoke it before uploading the video
  and include the exposure timestamp with the submission.

## Recording preparation

### 1. Prepare the desktop

1. Disable notifications and enable Do Not Disturb.
2. Close personal applications and unrelated browser tabs.
3. Use a clean desktop background.
4. Increase the terminal font to approximately 18–20 px.
5. Use a clean browser profile or hide personal bookmarks and extensions.

### 2. Prepare Apify

Log into Apify before recording. Open these pages in separate tabs:

1. **Settings → API & Integrations**
2. **Google Maps Scraper** actor
3. **Actor runs / console**

The API token must remain masked when its page is shown.

### 3. Prepare Repo 1

Use a fresh clone of the exact skills-based pipeline repository supplied with
the product. Do not record from a working directory containing unrelated files.

The required files are:

```text
skills/source_leads/SKILL.md
pipeline/source_bakeries.py
pipeline/providers/apify.py
fixtures/apify_bakeries_25.json
```

Do not add the live token yet.

## Free rehearsal before recording

From the Repo 1 root, run the complete command before adding `.env`:

```bash
python3 -m pipeline.source_bakeries \
  --query bakeries \
  --location "Chicago, Illinois" \
  --limit 25 \
  --out out/raw_bakery_leads.csv \
  --handoff out/leads.json
```

With no `APIFY_TOKEN`, the script must use the bundled fixture and report:

```text
received 23 raw bakery leads from 25 Google Maps rows
✅ 23 raw bakery leads → out/raw_bakery_leads.csv
✅ pipeline handoff → out/leads.json · REHEARSAL FIXTURE
```

The fixture holds 25 rows and two of them repeat an earlier listing, so the
rehearsal reports 23 leads. That is the script working, not a fault.

This rehearsal makes no network request and spends nothing. The later live run
will overwrite the two output files.

Do not begin recording until the free rehearsal succeeds.

## Exact recording sequence

### Scene 1 — Establish the clean machine

Start recording and hold the clean terminal for three seconds.

Run:

```bash
claude --version
python3 --version
git --version
```

Then enter the fresh Repo 1 clone:

```bash
cd <REPO_1_CLONE>
```

Do not install any project packages. The demonstration should make the starting
environment explicit:

- Claude Code is installed.
- System Python is installed.
- Git is installed.
- A browser is available.
- The project has no installed runtime packages.

### Scene 2 — Start Claude Code

Run:

```bash
claude
```

Give Claude this exact prompt:

```text
Read skills/source_leads/SKILL.md and run only the sourcing stage.

Find 25 bakeries in Chicago, Illinois using Apify's Google Maps actor.
Save a human-readable CSV to out/raw_bakery_leads.csv and the
pipeline handoff to out/leads.json.

Do not enrich, qualify, or contact anyone.

Before running, show me the Python line that crosses from the local
system into the external Apify system.
```

Allow Claude to visibly read:

```text
skills/source_leads/SKILL.md
pipeline/source_bakeries.py
pipeline/providers/apify.py
```

Pause briefly whenever Claude displays one of those file paths.

### Scene 3 — Obtain the API key

Switch to Apify's **API & Integrations** page.

1. Show that the token begins with `apify_api_`.
2. Keep the remainder masked.
3. Click **Copy**.
4. Hold the masked-token page for approximately three seconds.

Never reveal or visibly paste the complete token.

### Scene 4 — Add the token without displaying it

Return to a normal macOS terminal. Write the clipboard contents to local `.env`
without echoing the token:

```bash
printf 'APIFY_TOKEN=' > .env
pbpaste >> .env
printf '\n' >> .env
```

Confirm that `.env` contains an Apify-shaped value without printing the value:

```bash
python3 -c "from pathlib import Path; ok=Path('.env').read_text().startswith('APIFY_TOKEN=apify_api_'); print('APIFY_TOKEN=apify_api_' + '•'*12 if ok else 'TOKEN NOT SET')"
```

Expected visible output:

```text
APIFY_TOKEN=apify_api_••••••••••••
```

Confirm that Git ignores the file:

```bash
git check-ignore .env
```

Expected output:

```text
.env
```

Never run `cat .env`.

### Scene 5 — Show the local-to-external boundary

Have Claude display the relevant section of:

```text
pipeline/source_bakeries.py
```

The important line is:

```python
items = provider.run_actor(actor, run_input, limit=limit)
```

Pause on this line for two or three seconds.

Then display the relevant section of:

```text
pipeline/providers/apify.py
```

The lower-level external boundary is:

```python
with urllib.request.urlopen(req, timeout=60) as resp:
```

Pause on this line for two or three seconds.

These lines will receive additional highlights and labels during integration:

```text
LOCAL SYSTEM → authenticated HTTP request → EXTERNAL APIFY SYSTEM
```

### Scene 6 — Start the genuine Apify run

Run:

```bash
python3 -m pipeline.source_bakeries \
  --query bakeries \
  --location "Chicago, Illinois" \
  --limit 25 \
  --out out/raw_bakery_leads.csv \
  --handoff out/leads.json
```

The terminal should visibly establish:

```text
provider apify LIVE
actor=compass/crawler-google-places
query='bakeries'
location='Chicago, Illinois'
```

This POST starts a real Apify actor and may incur an Apify charge.

### Scene 7 — Show Apify doing the external work

Immediately switch to the Apify run console.

Record continuously while the console shows:

1. The actor starting
2. `READY` or its initial state
3. `RUNNING`
4. The dataset count increasing
5. 25 results reached
6. `SUCCEEDED`

Do not stop the recording during the wait. Uneventful sections will be
accelerated during editing while preserving the important state changes.

Remain on the successful run for approximately four seconds.

### Scene 8 — Return to the terminal

Return to the terminal after the actor succeeds.

The completed command should report:

```text
received 25 raw bakery leads from 25 Google Maps rows
✅ 25 raw bakery leads → out/raw_bakery_leads.csv
✅ pipeline handoff → out/leads.json · LIVE APIFY
```

A live run may report fewer leads than rows. Google Maps sometimes lists one
shop twice, and the script keeps the first listing only. Say so in the
submission if it happens; do not re-run to chase a round number.

Hold this output for three seconds.

### Scene 9 — Preview the real records

Show the number of CSV records and the first five bakery names:

```bash
python3 -c "import csv; p='out/raw_bakery_leads.csv'; rows=list(csv.DictReader(open(p))); print(f'{len(rows)} RAW LEADS'); [print(r['name'], '—', r['website'] or 'NO WEBSITE') for r in rows[:5]]"
```

The first line gives the count, and it must match the number the command
printed in Scene 8:

```text
25 RAW LEADS
```

Then show the pipeline handoff schema and counts:

```bash
python3 -c "import json; d=json.load(open('out/leads.json')); print(d['schema']); print(d['counts'])"
```

Expected shape:

```text
claude3d.raw-business-leads.v1
{'dataset_items': 25, 'leads': 25}
```

Hold the final terminal for five seconds, then stop recording.

## Submission checklist

Submit these three files:

```text
apify_real_run_raw.mp4
raw_bakery_leads.csv
leads.json
```

Do not submit:

```text
.env
APIFY_TOKEN
```

Before uploading, verify:

- The video is 1920×1080 or higher.
- The full token is never visible.
- The Apify run reaches 25 results and `SUCCEEDED`.
- The terminal reports its raw lead count (25, or fewer if a listing repeated).
- The submitted CSV contains that many data rows.
- The submitted JSON's `counts.leads` is the same number.
- No personal email, billing data, or unrelated browser content is visible.

If the key was exposed in any frame, revoke it before submitting the recording
and include the exposure timestamp so the affected section can be removed.
