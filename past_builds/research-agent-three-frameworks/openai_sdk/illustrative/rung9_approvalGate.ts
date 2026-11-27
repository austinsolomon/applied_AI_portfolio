// ILLUSTRATIVE - not run
//
// Rung 9, "A human gate before anything is sent". The lesson the course repeats: writing "ask first"
// in a prompt is not a gate. The database and the dispatcher enforce the pause, and the payload hash
// binds the human's decision to the exact message that will go out - here, an email to a bakery
// owner that nobody has read yet.

// In the outbound tool dispatcher, before any external side effect:
if (call.name === 'send_email') {
  const action = await db.createPendingAction({
    workspaceId, callId: call.call_id,
    arguments: validatedEmail, payloadHash: hash(validatedEmail),
    loopCheckpoint: input,
  });
  return { status: 'waiting_approval', actionId: action.id };
}

// Approval route: compare-and-set pending -> approved inside a transaction.
await db.approveAndEnqueueOutbox({ actionId, reviewerId, expectedPayloadHash });

// Outbox worker: delivery provider receives a stable idempotency key.
const delivery = await mailer.send(approvedAction.arguments, {
  idempotencyKey: approvedAction.id,
});
await db.recordDeliveryAndQueueResume(approvedAction, delivery);
// The resumed loop appends { type: 'function_call_output', call_id, output }.
