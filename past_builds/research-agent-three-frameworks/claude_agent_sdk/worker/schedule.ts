// ILLUSTRATIVE - not run. Rung 8: the Monday rerun.
//
// Needs the `node-cron` package (not a dependency of this build) and the rung 7 enqueue.
// See docs/rung_status.md.

// worker/schedule.ts - every Monday 09:00 UTC
import cron from 'node-cron';

cron.schedule('0 9 * * 1', () => enqueueColumn(ORDERING_COLUMN_ID), { timezone: 'UTC' });

// rung 8 also validates every inbound webhook payload before it costs anything:
// (zodSchema is `import * as zodSchema from 'zod'`, the same import the runnable rungs use)
const inboundLeadSchema = zodSchema.z.object({
  bakeryName: zodSchema.z.string(), // 'Bluebird Bakery'
  bakeryWebsiteUrl: zodSchema.z.string().url(), // 'https://bluebird-bakery.example/'
  ownerEmail: zodSchema.z.string().email(), // 'hannah@bluebird-bakery.example'
});
