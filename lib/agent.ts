import { ToolLoopAgent } from 'ai';
import { createBashTool } from './tools';
import { Sandbox } from '@vercel/sandbox';

// Free-tier model; course default is 'anthropic/claude-opus-4.6' (needs paid credits)
const MODEL = 'nvidia/nemotron-3.5-lightning-free';

const sandbox = await Sandbox.create();

export const agent = new ToolLoopAgent({
  model: MODEL,
  instructions: '',
  tools: {
    bashTool: createBashTool(sandbox)
  }
});
