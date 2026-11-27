# From the Outreach tower to real tools

You finished Source, Enrich, Qualify and Outreach. Python runs that workflow;
a Claude skill supplies instructions for using it. Now connect Outreach's
actions as tools, using the same request-and-result pattern as the game.

A **tool** is a function a program can ask to run. A **tool call** names that
function and supplies the information it needs. **MCP**, Model Context Protocol,
is a standard way for an AI application to discover tools, call them and receive
results. A **server** is the program making the tools available. Here it runs on
your own computer through local process pipes, called **stdio**.

The tower coordinates the task. The drone shows a request going out and a result
coming back. The email trucks do the delivery work. Several email batches still
use one channel: email. One MCP server can expose all four tools below.

| Tool | Information it needs | What it establishes |
|---|---|---|
| `prepare_checked_email` | `lead_id`, the bakery's saved ID | A checked draft and recipient, or hold reasons. |
| `send_checked_email` | `lead_id`, returned `draft_id`, and `recipient` | Acceptance for fictional delivery. Arrival and reply remain unknown. |
| `read_outreach_results` | The same `lead_id` | Saved delivery, bounce and reply evidence for that message. |
| `record_confirmed_meeting` | `lead_id`, `starts_at`, and the separate `confirmation` | One supported meeting, with zero customers. |

The functions reuse `ICP.check`, `lint`, the saved writer/sender and the existing
meeting checks. Unknown or rejected records cannot be sent. An empty or different
recipient fails. Repeated requests return the existing send or meeting. SQLite,
Python's built-in file database, stores this history in
`out/mcp_outreach.sqlite3`, separately from the ordinary pipeline outputs. Server counts describe the saved
ledger history; they do not silently reset when an input file changes.

## Run the free local connection exercise

Use Python 3.11 or newer. Run inside this repository:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[mcp]"
python3 -m pipeline.run_pipeline --offline
python3 -m pipeline.mcp_demo
```

On Windows, use `py -m venv .venv` and activate with
`.venv\Scripts\Activate.ps1` in PowerShell, then type `python` where these
commands say `python3`.
A **virtual environment** is a folder containing this project's Python libraries.
The install downloads libraries once. MCP is pinned at 2.2.0. The original
Python-only exercise still needs no additional library.

The demo starts a real MCP server and client, discovers four tools and calls
them locally. It uses saved course results: no live model, website, email service
or calendar is contacted, even when service keys exist on the computer.

The current baseline ends with **8 sent, 5 delivered, 2 replies, 1 confirmed
meeting and 0 customers**. Night Owl's draft is held. Honeycomb replies without
a confirmed time. Sunrise has separate scheduling evidence. The demonstration
reads that evidence before recording its meeting; sending alone cannot do so.
A changed input cohort can legitimately produce different counts.

Open `out/mcp_tool_trace.json` for each actual request and result, and
`out/mcp_outreach.json` for the summary. Repeating the demo preserves the same
send/meeting IDs. If you change which bakery IDs are in the input, the demo
asks for a separate output folder so old receipts cannot distort the new totals.
For an independent new scenario, use
`python3 -m pipeline.mcp_demo --outdir out/another-mcp-run`.

The baseline pipeline and this connection exercise keep separate run files.
Do not add their counts together: they rehearse the same example twice.

## Connect the same tools to Claude Code

Keep the virtual environment active, then run:

```sh
python3 -m pipeline.configure_mcp
claude
```

The setup writes project-local `.mcp.json` with the exact Python and input paths.
It preserves unrelated tool-server entries. Review and approve this local
connection when Claude asks, then use `/mcp` to check its status. If you move
the repository, rerun setup from its new location.

Try: “Use the course-outreach tools to prepare Sunrise's checked email. Show me
the draft and recipient before the fictional send. Then read its results;
show me the separate confirmation before recording a meeting.”

The server remains a saved-data rehearsal. Claude itself uses your own account
and may incur model usage; the Python demo does not. A verified local MCP
exchange does not prove Claude authentication or live model behavior. See the
[official Claude MCP setup guide](https://code.claude.com/docs/en/mcp).

## What comes next

A **skill** gives instructions. A **tool** performs a defined action. **MCP**
connects the application to tools. An **agent** uses a model to choose its next
action from what it has learned. A skill can call an agent.

The next repository returns to Enrich. Our pipeline has used saved research
answers; now you build the researcher that produced them. Instead of receiving
an email result, its `fetch_url` tool returns page text. The agent decides whether
that text answers the ordering question or whether it needs another page.
Check its source before accepting its answer into the same pipeline.

That project's offline demo replays recorded model choices and runs its tool
handlers. Its optional live path uses Claude Agent SDK and the real MCP tool
server. Finding a page does not qualify or email a bakery.
