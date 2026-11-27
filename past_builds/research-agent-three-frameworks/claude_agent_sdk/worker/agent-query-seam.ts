// The seam in front of query().
//
// Why a seam exists here at all: query() in @anthropic-ai/claude-agent-sdk does not make a plain
// network call. It starts a separate `claude` program on your computer and talks to it over stdio.
// There is no model provider underneath it to swap out, the way the other two builds in this repo
// swap one. So offline mode replaces query() ITSELF with a stand-in of the same shape, fed from the
// recordings in shared/fixtures/recorded_model_responses/.
//
// Everything written around the SDK - prompt assembly, tool wiring, the tool handlers, schema
// validation, the judge - runs for real in both modes. The SDK, the subprocess and the `claude`
// binary run only in live mode.

import { query } from '@anthropic-ai/claude-agent-sdk';
import type { Options } from '@anthropic-ai/claude-agent-sdk';

/** 'offline' replays a recorded agent session. 'live' starts a real claude process and costs money. */
export type AgentRunMode = 'offline' | 'live';

/** The env var that turns live mode on. Anything other than "live" means offline. */
export const AGENT_RUN_MODE_ENV_VAR_NAME = 'RESEARCH_AGENT_RUN_MODE';

export function readAgentRunModeFromEnvironment(): AgentRunMode {
  return process.env[AGENT_RUN_MODE_ENV_VAR_NAME] === 'live' ? 'live' : 'offline';
}

/**
 * The slice of the SDK's message stream this build actually reads. The real SDKMessage union has
 * many more members and many more fields; every field named here exists on the real messages with
 * the same name, so the same reading code works against both the SDK and the replay stand-in.
 */
export interface AgentSdkMessage {
  type: 'assistant' | 'user' | 'result' | 'system' | string;
  subtype?: string;
  /** On 'assistant' and 'user' messages: the Anthropic-API-shaped message for the trace. */
  message?: unknown;
  /** On a successful 'result' message: the final text. */
  result?: string;
  /** On a 'result' message produced with outputFormat: { type: 'json_schema' }. */
  structured_output?: unknown;
  total_cost_usd?: number;
  num_turns?: number;
  session_id?: string;
}

export interface AgentQueryRequest {
  prompt: string;
  options: Options;
}

/** Same call shape as the SDK's query(): one request in, a stream of messages out. */
export type AgentQueryFunction = (request: AgentQueryRequest) => AsyncIterable<AgentSdkMessage>;

/** Live mode: the real SDK. Starts a `claude` process, calls the Anthropic API, costs money. */
export const liveAgentQuery: AgentQueryFunction = (request) =>
  query(request) as unknown as AsyncIterable<AgentSdkMessage>;
