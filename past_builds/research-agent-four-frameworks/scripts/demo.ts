// The required course rehearsal always stays offline, regardless of saved keys.
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { loadAllCompanies } from '../shared/replay/index.ts';
import { runResearch } from '../claude_agent_sdk/worker/run-research.ts';
import { judgeLead } from '../claude_agent_sdk/worker/judge-lead.ts';
const evidence = JSON.parse(await readFile(new URL('../shared/fixtures/pipeline_evidence.json', import.meta.url), 'utf8'));
const answers = [];
console.log('OFFLINE REHEARSAL: saved model choices and saved pages. No live model, no network.');
for (const company of loadAllCompanies()) {
  const research = await runResearch({ companyId: company.companyId, runMode: 'offline' });
  const result = await judgeLead({ companyId: company.companyId, researchReport: research.researchReport, runMode: 'offline' });
  const source = evidence.answers.find((answer: any) => answer.lead_id === company.pipelineLeadId);
  if (!source) throw new Error(`Missing pipeline evidence for ${company.companyName}`);
  const { judgeCostUsd, ...verdict } = result;
  if (!verdict.sourceUrls.includes(source.evidence.source_url)) throw new Error('Evidence must be in the cited pages.');
  answers.push({ ...source, verdict });
  console.log(`${company.companyName}: ${research.executedToolCalls.length} tool calls; supportsNoOnlineOrdering=${verdict.supportsNoOnlineOrdering}`);
  console.log(`  ${verdict.researchReason}`);
}
await mkdir('out', { recursive: true });
await writeFile('out/research_agent_answers.json', JSON.stringify({ field: 'ordering_platform', mode: 'offline_replay', answers }, null, 2) + '\n');
console.log(`Saved ${answers.length} answers: ${resolve('out/research_agent_answers.json')}`);
console.log('Import into Repo 1, inspect the source, then confirm. Nothing is qualified or emailed here.');
