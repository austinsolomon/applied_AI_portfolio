// ILLUSTRATIVE - not run
//
// Rung 4 — somewhere to run it. In the real product this file is app/api/run-cell/route.ts inside a
// Next.js project: one file exporting POST, which becomes a Vercel Function when deployed.
//
// Why it is not run here: this repo is a plain Node project with no Next.js app, no deployment and
// no database. To run it you would create a Next.js project, put this file at that path, and deploy
// it to Vercel.
//
// Worth knowing: Vercel bills a Function for active CPU, not for time spent waiting on a model or a
// scrape. An agent is almost all waiting, which is what keeps this cheap. maxDuration is the
// ceiling for one request; a twelve-step agent usually fits, a slow site or a retry may not, and a
// timeout loses the whole run. That is the problem rung 6 solves.

import { researchAgent } from '@/lib/research-agent';
import { judgeLead } from '@/lib/judge-lead';

export const maxDuration = 300; // seconds; the ceiling for one request

export async function POST(request: Request) {
  const { researchPrompt, qualificationRubric } = await request.json();

  const researchRun = await researchAgent.generate({ prompt: researchPrompt });
  const qualificationVerdict = await judgeLead(researchRun.text, qualificationRubric);

  return Response.json({ researchReport: researchRun.text, ...qualificationVerdict });
}
