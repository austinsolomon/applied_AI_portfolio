// ILLUSTRATIVE - not run. Rung 4 (somewhere to run it) and rung 8 (the inbound webhook).
//
// Nothing in this file is executed, tested or imported by the runnable rungs. It is the artifact
// page's design sketch, kept here so the shape is on disk. Making it run needs: a container host, a
// Postgres database, insertRow/readRequestBody, and a real ANTHROPIC_API_KEY. See docs/rung_status.md.

// worker/server.ts - runs in a container, not in a serverless function
import { createServer } from 'node:http';
import { runResearch } from './run-research';
import { judgeLead } from './judge-lead';

createServer(async (request, response) => {
  // rung 8 - the webhook: a bakery lead arrives from somewhere else and becomes a queued cell
  if (request.method === 'POST' && request.url === '/inbound-lead') {
    if (request.headers['x-webhook-secret'] !== process.env.INBOUND_WEBHOOK_SECRET) {
      response.writeHead(403).end();
      return;
    }
    // reject bad payloads before they cost anything
    const inboundLead = inboundLeadSchema.parse(JSON.parse(await readRequestBody(request)));
    const insertedRowId = await insertRow(inboundLead);
    await sql`insert into cell_jobs (row_id, column_id) values (${insertedRowId}, ${ORDERING_COLUMN_ID})`;
    response.writeHead(202).end(); // accepted; the worker pool does the rest
    return;
  }

  // rung 4 - run one cell now
  if (request.method !== 'POST' || request.url !== '/run-cell') {
    response.writeHead(404).end();
    return;
  }

  const { researchPrompt, qualificationRubric } = JSON.parse(await readRequestBody(request));
  const researchRun = await runResearch(researchPrompt, 'claude-sonnet-5');
  const qualificationVerdict = await judgeLead(researchRun.researchReport, qualificationRubric);

  response.writeHead(200, { 'content-type': 'application/json' });
  response.end(JSON.stringify({ researchReport: researchRun.researchReport, ...qualificationVerdict }));
}).listen(8080);
