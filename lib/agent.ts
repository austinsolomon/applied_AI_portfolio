import { ToolLoopAgent } from 'ai';
import { createBashTool } from './tools';
import { Sandbox } from '@vercel/sandbox';
import { anthropic } from '@ai-sdk/anthropic';
import path from 'path';
import fs from 'fs/promises';

// Course uses 'anthropic/claude-opus-4.6' via AI Gateway (needs paid gateway credits).
// We call Anthropic directly instead; reads ANTHROPIC_API_KEY from .env.local.
const MODEL = anthropic('claude-sonnet-5-5');

const sandbox = await Sandbox.create();
const INSTRUCTIONS = `
You are a helpful assistant that answers questions about customer calls. Use bashTool to explore the files and find relevant information pertaining to the user's query. Using the information you find, craft a response for the user and output it as text.
`;

await loadSandboxFiles(sandbox);

export const agent = new ToolLoopAgent({
  model: MODEL,
  instructions: INSTRUCTIONS,
  tools: {
    bashTool: createBashTool(sandbox)
  }
});

// Copy lib/calls/*.md from the server's disk into the sandbox at calls/
async function loadSandboxFiles(sandbox: Sandbox) {
  const callsDir = path.join(process.cwd(), 'lib', 'calls');
  const callFiles = await fs.readdir(callsDir);

  for (const file of callFiles) {
    const filePath = path.join(callsDir, file);
    const buffer = await fs.readFile(filePath);
    await sandbox.writeFiles([{ path: `calls/${file}`, content: buffer }]);
  }
}
