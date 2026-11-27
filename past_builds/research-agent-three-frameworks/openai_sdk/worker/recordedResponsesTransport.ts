// The offline seam for this build.
//
// The `openai` package is a real HTTP client: every call to `client.responses.create()` or
// `client.responses.parse()` ends up calling `fetch`. The SDK lets you hand it your own `fetch`
// function. That is the whole trick here: we give the real SDK a `fetch` that never touches the
// network and instead answers from `shared/fixtures/recorded_model_responses/`.
//
// Everything above that line is the real thing - the real SDK builds the real request body, parses
// the reply, aggregates `output_text`, and (in rung 3) validates and fills `output_parsed`. Only the
// wire is replaced. That is why rungs 0-3 need no API key and no network.

import {
  openReplayModelSession,
  type CompanyId,
  type RecordedModelTurn,
  type ReplayModelSession,
  type RungId,
} from '../../shared/replay/index.ts';

/** The one URL the Responses API is reached at; anything else is a mistake in this build. */
const RESPONSES_API_PATH_SUFFIX = '/responses';

/** A single request the SDK made, kept so tests can read what the build actually asked for. */
export interface RecordedTransportRequest {
  requestUrl: string;
  requestBody: Record<string, unknown>;
}

export interface RecordedResponsesTransport {
  /** Hand this to `new OpenAI({ fetch: ... })`. */
  replayFetch: (requestUrl: string | URL | Request, requestInit?: RequestInit) => Promise<Response>;
  /** The replay session this transport reads turns from. */
  replaySession: ReplayModelSession;
  /** Every request the SDK sent through this transport, oldest first. */
  requestsSent: RecordedTransportRequest[];
}

/**
 * Turns one recorded turn into a Responses API payload the real SDK can parse.
 *
 * A recorded turn is provider-neutral on purpose (see shared/FIXTURE_CONTRACT.md §7), so this
 * function is the OpenAI-shaped adapter the contract says lives in the build folder.
 */
export function buildResponsesPayloadFromRecordedTurn(
  recordedTurn: RecordedModelTurn,
  conversationId: string,
): Record<string, unknown> {
  const assistantMessageText =
    recordedTurn.finalAnswer?.answerKind === 'json'
      ? JSON.stringify(recordedTurn.finalAnswer.answerJson)
      : (recordedTurn.finalAnswer?.answerText ?? recordedTurn.assistantText);

  const outputItems: Record<string, unknown>[] = [
    {
      type: 'message',
      id: `msg_${conversationId}_turn${recordedTurn.turnIndex}`.replace(/[^A-Za-z0-9_]/g, '_'),
      status: 'completed',
      role: 'assistant',
      content: [{ type: 'output_text', text: assistantMessageText, annotations: [] }],
    },
  ];

  for (const requestedToolCall of recordedTurn.toolCallsRequested) {
    outputItems.push({
      type: 'function_call',
      id: `fc_${requestedToolCall.toolCallId}`,
      call_id: requestedToolCall.toolCallId,
      name: requestedToolCall.toolName,
      arguments: JSON.stringify(requestedToolCall.toolArguments),
      status: 'completed',
    });
  }

  return {
    id: `resp_${conversationId}_turn${recordedTurn.turnIndex}`.replace(/[^A-Za-z0-9_]/g, '_'),
    object: 'response',
    created_at: 0,
    status: 'completed',
    model: 'recorded-fixture-model',
    output: outputItems,
    parallel_tool_calls: true,
    tool_choice: 'auto',
    tools: [],
    usage: {
      input_tokens: 0,
      input_tokens_details: { cached_tokens: 0 },
      output_tokens: 0,
      output_tokens_details: { reasoning_tokens: 0 },
      total_tokens: 0,
    },
  };
}

/**
 * Builds a `fetch` that answers Responses API calls from one recorded conversation.
 *
 * One fetch call = one recorded turn, in order. Ask for more turns than were recorded and the
 * shared replay layer throws ReplayExhaustedError, which means your loop ran more times than the
 * recording expects - a bug to fix, not an error to swallow.
 */
export function createRecordedResponsesTransport(
  rungId: RungId,
  companyId: CompanyId,
): RecordedResponsesTransport {
  const replaySession = openReplayModelSession(rungId, companyId);
  const requestsSent: RecordedTransportRequest[] = [];

  const replayFetch = async (
    requestUrl: string | URL | Request,
    requestInit?: RequestInit,
  ): Promise<Response> => {
    const requestUrlText =
      typeof requestUrl === 'string'
        ? requestUrl
        : requestUrl instanceof URL
          ? requestUrl.toString()
          : requestUrl.url;

    if (!new URL(requestUrlText).pathname.endsWith(RESPONSES_API_PATH_SUFFIX)) {
      throw new Error(
        `The offline build only replays the Responses API, but something called ${requestUrlText}.`,
      );
    }

    const requestBodyText = typeof requestInit?.body === 'string' ? requestInit.body : '{}';
    requestsSent.push({
      requestUrl: requestUrlText,
      requestBody: JSON.parse(requestBodyText) as Record<string, unknown>,
    });

    const recordedTurn = replaySession.takeNextTurn();
    const responsePayload = buildResponsesPayloadFromRecordedTurn(
      recordedTurn,
      replaySession.conversationId,
    );

    return new Response(JSON.stringify(responsePayload), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };

  return { replayFetch, replaySession, requestsSent };
}
