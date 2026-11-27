// ILLUSTRATIVE - not run
//
// Rung 8 — triggers: a webhook, a schedule, a button. The front door. All three end in the same
// start() call from rung 6.
//
// Why it is not run here: it needs a deployed Next.js app (for the route), Vercel's cron
// configuration, Vercel BotID, and rung 6's workflow.
//
// The habit to copy: validate the incoming body with a schema BEFORE anything costs money. A
// webhook is a public URL, and a public URL gets junk.

import { checkBotId } from 'botid/server';
import { start } from 'workflow/api';

export async function POST(request: Request) {
  const botVerification = await checkBotId();
  if (botVerification.isBot) return Response.json({ error: 'denied' }, { status: 403 });

  const inboundLead = inboundLeadSchema.parse(await request.json()); // reject bad payloads early
  const insertedRowId = await insertRow(inboundLead);
  await start(runCell, [insertedRowId, ORDERING_COLUMN_ID]);
  return Response.json({ accepted: true });
}

// vercel.json: every Monday at 09:00 UTC. Cron sends a GET, so point it at a GET route that calls
// the same dispatcher:
//   { "crons": [{ "path": "/api/cron/weekly-rerun", "schedule": "0 9 * * 1" }] }
