// ILLUSTRATIVE - not run. Rung 9: a human gate before anything is sent.
//
// This is the "prompts persuade, tools enforce" lesson in SDK form: send_email is deliberately left
// out of allowedTools, so it must come through canUseTool, where a person clicks. Needs a Slack app,
// an outreach tool server and a suppression list. See docs/rung_status.md.

import { query, type HookCallback, type PreToolUseHookInput } from '@anthropic-ai/claude-agent-sdk';

const pendingApprovals = new Map<string, (decision: ApprovalDecision) => void>();

// a rule that needs no human: never email a bakery owner who asked not to be written to
const blockSuppressedDomains: HookCallback = async (hookInput) => {
  const emailInput = (hookInput as PreToolUseHookInput).tool_input as { recipientEmail: string };
  if (!(await isSuppressed(emailInput.recipientEmail))) return {};
  return {
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: 'deny',
      permissionDecisionReason: 'recipient is on the suppression list',
    },
  };
};

for await (const message of query({
  prompt:
    `Research how the bakery in row ${rowId} takes orders today. If the evidence supports no online ` +
    'ordering, draft a first email to its owner about taking orders online, and send it with send_email.',
  options: {
    mcpServers: { research: researchToolServer, outreach: outreachToolServer },
    allowedTools: ['WebSearch', 'mcp__research__fetch_url'], // send_email is NOT here, so it must ask
    hooks: { PreToolUse: [{ matcher: 'mcp__outreach__send_email', hooks: [blockSuppressedDomains] }] },
    canUseTool: async (toolName, toolInput) => {
      if (toolName !== 'mcp__outreach__send_email') return { behavior: 'deny', message: 'tool not permitted' };
      await postDraftToSlack(rowId, toolInput); // message with Approve / Edit / Reject buttons
      const approvalDecision = await new Promise<ApprovalDecision>((resolveApproval) =>
        pendingApprovals.set(`approval:${rowId}`, resolveApproval),
      ); // the run waits here until a click
      return approvalDecision.approved
        ? { behavior: 'allow', updatedInput: approvalDecision.editedInput ?? toolInput }
        : { behavior: 'deny', message: `reviewer rejected: ${approvalDecision.reviewerComment}` };
    },
  },
})) {
  /* ... */
}

// the Slack button handler, elsewhere in the same worker
pendingApprovals.get(`approval:${rowId}`)?.({ approved: true, editedInput, reviewerComment: '' });
