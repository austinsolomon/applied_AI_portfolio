# Optional extensions after the required Claude build

The course ends when you can research a bakery, inspect the cited evidence and
return a checked value to Enrich. Everything below is optional.

- `vercel_ai_sdk/` and `openai_sdk/`: alternate implementations of the same task.
  Their existing local tests use the same fixtures. Install/test inside that
  folder only if you want to compare libraries; root `npm ci` installs Claude.
- Claude files marked `ILLUSTRATIVE - not run`: sketches for hosting, stored jobs,
  scheduling and browser tools. They are not working services or course setup
  steps. `claude_agent_sdk/docs/rung_status.md` lists what each would require.

A queue is a list of jobs waiting to run. Hosting means keeping your program on a
computer that can run it when needed. Neither is required to learn the research
loop on your own machine. No commercial product walkthrough is bundled here.
