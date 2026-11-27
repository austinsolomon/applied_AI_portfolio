# Course project

Start with `python3 -m pipeline.run_pipeline --offline`. This must not call paid
services even if keys exist. The four project skills live in `.claude/skills/`.
Use Source → Enrich → Qualify → Outreach, passing the saved JSON file each time.
Do not invent missing data, treat unknown as no, or send rejected/review rows to
Outreach. A reply is not a confirmed meeting and a meeting is not a customer.
A skill invocation may use the learner's Claude account; the Python rehearsal
itself is local, uses saved practice data, and costs nothing.

For the tool-connection exercise, follow docs/5_tools_mcp.md. The local
course-outreach MCP server always uses saved practice data, even with keys set.
Prepare before sending, inspect the result, then show the separate confirmation
before recording a meeting. A successful tool call is not proof of delivery or
interest. The MCP ledger is a separate rehearsal; never add its counts to the
ordinary pipeline run. The next repository uses MCP tools to research one Enrich
field, and imports still require a human evidence check.
