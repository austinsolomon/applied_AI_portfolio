// ILLUSTRATIVE - not run
//
// Rung 8, "Triggers: a webhook, a schedule, a button". Three ways in, one enqueue path. The SDK has
// no trigger layer of its own; this is ordinary application plumbing.

// Application pseudocode: all callers use the same enqueue policy.
const event = await webhooks.verifyAndParse(request);
await db.transaction(async tx => {
  if (await tx.hasInboundEvent(event.id)) return;
  const row = await tx.upsertLead(event.workspaceId, event.lead);
  await tx.enqueueEligibleCells(row, { reason: 'webhook' });
  await tx.recordInboundEvent(event.id);
});

// Called by your scheduler at the configured time and timezone.
await enqueueEligibleRows({ tableId, reason: 'scheduled-refresh' });
// The grid's Run button calls the authenticated route from rung 4.
