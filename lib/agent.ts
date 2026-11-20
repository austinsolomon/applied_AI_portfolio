import { ToolLoopAgent } from 'ai';

// Free-tier model; course default is 'anthropic/claude-opus-4.6' (needs paid credits)
const MODEL = 'nvidia/nemotron-3.5-lightning-free';

export const agent = new ToolLoopAgent({
  model: MODEL,
  instructions: '',
  tools: {}
});
