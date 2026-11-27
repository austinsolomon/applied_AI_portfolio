// ILLUSTRATIVE - not run
//
// Rung 9 — a human gate before anything is sent. The workflow pauses, a person clicks Approve or
// Reject in Slack, and only then does the email go out.
//
// Why it is not run here: defineHook is part of Vercel's Workflow runtime, and the pause only works
// inside it. There is also no Slack app and no outbox in this build.
//
// The lesson the course keeps coming back to: prompts persuade, tools enforce. "Do not send
// anything without approval" in an instruction is a request. A gate the send has to pass through is
// a rule. While the run is paused it costs nothing — it is asleep, not spinning.

import { defineHook } from 'workflow';
import * as zodSchema from 'zod';

export const approvalHook = defineHook({
  schema: zodSchema.object({
    approved: zodSchema.boolean(),
    reviewerComment: zodSchema.string(),
  }),
});

export async function researchAndReply(rowId: number) {
  'use workflow';
  const emailDraft = await writeEmail(rowId);

  using approvalPause = approvalHook.create({ token: `approval:${rowId}` });
  await postDraftToSlack(emailDraft, approvalPause.token); // a message with Approve / Reject buttons
  const approvalDecision = await approvalPause; // the run sleeps here, for free, until a click

  if (approvalDecision.approved) await sendEmail(rowId, emailDraft);
}

// app/api/slack/route.ts: the button handler resumes the sleeping run.
await approvalHook.resume(`approval:${rowId}`, { approved: true, reviewerComment: '' });
