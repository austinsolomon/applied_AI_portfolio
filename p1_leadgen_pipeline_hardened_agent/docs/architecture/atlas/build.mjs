// Adapted from inkboard/system-atlas. See LICENSE.system-atlas and README.md.
// One catalog generates three connected maps and their shared text reference.
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { VIEWS, COMPONENTS, REPO, BUILD, SAMPLE } from './data.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const output = join(here, '..');
const template = readFileSync(join(here, 'template.html'), 'utf8');
const check = process.argv.includes('--check');
const files = new Map();
const plain = text => text.replace(/<code>(.*?)<\/code>/g, '`$1`').replace(/<mark>(.*?)<\/mark>/g, '**$1**').replace(/<[^>]+>/g, '');
const json = value => JSON.stringify(value).replace(/</g, '\\u003c');
const viewById = Object.fromEntries(VIEWS.map(view => [view.id, view]));
const sourceUrl = path => `${REPO}/blob/main/p1_leadgen_pipeline_hardened_agent/${path}`;

for (const view of VIEWS) {
  const ids = new Set(view.NODES.map(node => node.id));
  assert.equal(ids.size, view.NODES.length, `Duplicate component in ${view.id}`);
  const visible = new Set();
  const validateHop = (hop, available) => {
    assert(available.has(hop[0]) && available.has(hop[1]), `Unrevealed flow endpoint in ${view.id}: ${hop}`);
  };
  for (const chapter of view.CH) {
    assert(chapter.reveal.length <= 3, 'Reveal at most three structures per chapter');
    for (const id of chapter.reveal) {
      assert(ids.has(id) && !visible.has(id), `Invalid or repeated chapter component ${id}`);
      visible.add(id);
    }
    for (const hop of chapter.flow || []) validateHop(hop, visible);
  }
  assert.equal(visible.size, ids.size, `Some components have no chapter in ${view.id}`);
  for (const flow of view.FLOWS) for (const hop of flow.hops) validateHop(hop, ids);
  for (const node of view.NODES) {
    for (const field of ['one', 'what', 'source', 'how', 'kind', 'short']) assert(node[field], `${node.id} needs ${field}`);
    assert(node.short.length <= 14, `Label too long: ${node.short}`);
    for (const link of node.links) {
      assert(viewById[link.view]?.NODES.some(target => target.id === link.node), `Broken cross-view link on ${node.id}`);
    }
  }
  const nodes = view.NODES.map(node => ({ ...node, sourceUrl: sourceUrl(node.source), links: node.links
    .filter(link => link.view !== view.id)
    .map(link => ({ ...link, href: `${viewById[link.view].file}#${link.node}` })) }));
  const nav = VIEWS.map(item => `<a href="${item.file}"${item.id === view.id ? ' aria-current="page"' : ''}>${item.title}</a>`).join('');
  const data = {
    GROUPS: view.GROUPS, NODES: nodes, FLOWS: view.FLOWS, CH: view.CH,
    HOW_HTML: `<div class="eyebrow">P1 · ${BUILD}</div><h1 class="t">${view.title}</h1><p>${view.question}</p><p>All stores shown here are logical responsibilities in one Postgres database, not separate database services.</p><p>Arrows show handoffs and control decisions; they do not imply separate deployed microservices.</p><p>The figures come from the ${SAMPLE.date} Phoenix sample. Packet JSON is illustrative and excludes contacts and credentials.</p><p><a href="../sample_run.md">Read the sample report</a> · <a href="SYSTEM.md">Text reference</a></p>`,
    DECISIONS_HTML: view.decisions.map(([axis, decision]) => `<li><b>${axis}.</b> ${decision}</li>`).join(''),
    STATS: [{ k:'Perspective', v:view.title }], TITLE: view.title,
  };
  const script = Object.entries(data).map(([name, value]) => `const ${name} = ${json(value)};`).join('\n');
  const html = template.replace('__TITLE__', `P1 · ${view.title}`).replace('/*__DATA__*/', script).replace('<!--__NAV__-->', nav);
  for (const [, js] of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) new Function(js);
  files.set(view.file, html);
}

const lines = ['# P1 architecture — three connected perspectives', '',
  `Generated from one component catalog. Implementation baseline: [${BUILD}](${REPO}/commit/${BUILD}).`, '',
  '## Explore', '', ...VIEWS.map(view => `- [${view.title}](${view.file}) — ${view.question}`), '',
  '## Boundaries', '',
  'These maps explain one Python application with one Postgres database. A store shape represents a logical data responsibility, not a separate service. Cloud deployment is planned. Packet payloads are representative; live totals are dated observations.', '',
  `Phoenix, ${SAMPLE.date}: **${SAMPLE.sourced} sourced → ${SAMPLE.qualified} qualified → ${SAMPLE.saved} saved**; ${SAMPLE.rejected} rejected and ${SAMPLE.review} review. Ledger: ${SAMPLE.ledger_usd} USD including ${SAMPLE.estimated_usd} USD in conservative estimates. See [the sample report](../sample_run.md).`, ''];
for (const view of VIEWS) {
  lines.push(`## ${view.title}`, '', view.question, '', '### Chapters', '');
  view.CH.forEach((chapter, index) => lines.push(`${index + 1}. **${chapter.title}** — ${chapter.lede}`));
  lines.push('', '### Decisions', '', '| Decision | Rationale |', '|---|---|');
  view.decisions.forEach(([axis, decision]) => lines.push(`| ${axis} | ${decision} |`));
  for (const flow of view.FLOWS) {
    lines.push('', `### Flow: ${flow.name}`, '', '| From | To | Handoff |', '|---|---|---|');
    flow.hops.forEach(hop => lines.push(`| ${COMPONENTS[hop[0]].name} | ${COMPONENTS[hop[1]].name} | ${hop[2]} |`));
  }
  lines.push('');
}
lines.push('## Shared component catalog', '');
for (const [id, node] of Object.entries(COMPONENTS)) {
  lines.push(`### ${id} · ${node.name}${node.ghost ? ' (planned)' : ''}`, '', node.one, '', node.what, '',
    `Source: [${node.source}](${sourceUrl(node.source)}).`, '', 'Steps:', '');
  node.steps.forEach(([name, detail], index) => lines.push(`${index + 1}. **${name}** — ${detail}`));
  lines.push('');
  node.cond.forEach((question, index) => lines.push(typeof question === 'string' ? `- **Q-${id}${index + 1}:** ${question}` : `- **Q-${id}${index + 1}:** ${question.q} — ${question.r}`));
  lines.push('');
}
lines.push('## Maintenance', '', 'Edit `atlas/data.mjs`, then run `node atlas/build.mjs`. Both the maps and this text reference are generated; do not edit their outputs. `node atlas/build.mjs --check` checks graph integrity, script syntax and generated-file freshness without rewriting files.', '');
files.set('SYSTEM.md', lines.join('\n').replace(/\n{3,}/g, '\n\n'));
for (const [file, contents] of files) {
  const path = join(output, file);
  if (check) assert.equal(readFileSync(path, 'utf8'), contents, `Stale output: ${file}; rebuild the atlas`);
  else writeFileSync(path, contents);
}
console.log(`${check ? 'Verified' : 'Built'} three connected maps and SYSTEM.md; links, chapter visibility and script syntax valid.`);
