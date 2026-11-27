// Optional real SDK execution. Never called by demo or tests.
import { parseArgs } from 'node:util';
import { mkdir, writeFile } from 'node:fs/promises';
import { runResearch } from '../claude_agent_sdk/worker/run-research.ts';
import { judgeLead } from '../claude_agent_sdk/worker/judge-lead.ts';
const { values } = parseArgs({ options: {
  'allow-paid': { type: 'boolean', default: false }, company: { type: 'string' },
  website: { type: 'string' }, city: { type: 'string', default: 'Chicago' },
  state: { type: 'string', default: 'IL' },
}});
if (!values['allow-paid'] || !values.company || !values.website) {
  console.error('Live research uses your Claude access and can cost money. Nothing has run.\nUsage: npm run research:live -- --allow-paid --company "Your bakery" --website "https://your-bakery.com"');
  process.exit(2);
}
const website = new URL(values.website);
if (!['http:', 'https:'].includes(website.protocol) || website.hostname.endsWith('.example')) throw new Error('Use a real public http/https website for live research. Fictional .example sites only work offline.');
const company = { companyId: 'live_research', companyName: values.company, websiteDomain: website.hostname, cityName: values.city!, stateCode: values.state!, industryLabel: 'Bakery', expectedOutcomeNote: 'Unknown: establish it from evidence.' };
console.log('LIVE: Claude Agent SDK chooses research actions. Research is limited to 12 turns/$0.25; the answer check to 3 turns/$0.10.');
const research = await runResearch({ companyId: company.companyId, company, runMode: 'live' });
const answer = await judgeLead({ companyId: company.companyId, researchReport: research.researchReport, runMode: 'live' });
await mkdir('out', { recursive: true });
await writeFile('out/live-research.json', JSON.stringify({ company, research, answer, status: 'awaiting_human_review' }, null, 2) + '\n');
console.log(JSON.stringify(answer, null, 2));
console.log('Saved out/live-research.json. Verify sources before using the answer. No pipeline value was changed and nothing was sent.');
