# Required Claude Agent SDK implementation

Start with the root [README](../README.md): `npm ci`, `npm test`, `npm run demo`.
The root demo also exports the evidence file that Repository 1 imports.

The four small optional steps here show how the implementation grows:
`npm run rung0` (no pages), `npm run rung1` (one page), `npm run rung2` (choose and
read more pages), and `npm run rung3` (return a checked answer format).
All default to saved example conversations. Use the root `research:live` command
for a real company; the old rung commands select fictional course fixtures.

## Offline mode replays a recorded agent session

Say this part plainly, because it is the one honest weakness of this build.

`query()`, this SDK's entry point, does not make a plain network call. It starts a separate `claude`
program on your computer and talks to it over a pipe. That program is what calls Anthropic. So unlike
the other two builds, there is no "model provider" underneath that could be swapped for a fake one.

So **offline mode replaces `query()` itself**. `worker/agent-query-seam.ts` defines the one function
shape the rest of the build calls; `worker/replay-agent-query.ts` is an offline stand-in of that shape
that reads a recorded conversation from `../shared/fixtures/recorded_model_responses/` and hands back
SDK-shaped messages in the recorded order.

What that means for you:

- **Runs for real, offline:** every line of this build's own code — the prompts, the tool definitions,
  the tool handlers (they really open the recorded pages and really run the recorded searches), the
  message-reading code, the Zod schema, the judge, the tests.
- **Does not run, offline:** the SDK itself, the `claude` subprocess, the built-in tools that live
  inside it, and any real model.

A passing test here proves your code around the SDK is right. It does not prove the SDK call works on
your machine. Only live mode proves that.


## Optional infrastructure designs

Files outside the four tested research steps may be marked `ILLUSTRATIVE - not run`.
They describe possible hosting and scheduling extensions. They are outside the
required course and do not become working services by installing this folder.
See `docs/rung_status.md` and the root `OPTIONAL.md`.
