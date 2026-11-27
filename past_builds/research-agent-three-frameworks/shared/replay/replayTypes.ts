// The interface every build imports. Nothing in this file is specific to Vercel, the Claude Agent
// SDK or the OpenAI SDK: it is the provider-neutral shape the recordings are stored in.
//
// The three builds import these types by relative path, for example:
//   import type { RecordedModelTurn } from '../../shared/replay/replayTypes.ts';
//
// If you change a shape here, change shared/FIXTURE_CONTRACT.md in the same edit. Three other
// people are building against that document.

/** One of the four fictitious bakeries, e.g. "bluebird_bakery". */
export type CompanyId = string;

/** A rung folder name under shared/fixtures/recorded_model_responses/. */
export type RungId =
  | 'rung0_one_model_call'
  | 'rung1_one_tool'
  | 'rung2_agent_loop'
  | 'rung3_judge_verdict';

// ---------------------------------------------------------------------------------------------
// Companies, the research question and the rubric
// ---------------------------------------------------------------------------------------------

export interface CompanyRecord {
  companyId: CompanyId;
  pipelineLeadId?: string;
  companyName: string;
  websiteDomain: string;
  cityName: string;
  stateCode: string;
  industryLabel: string;
  /** Why this company is in the fixture set: clearly settles true, clearly settles false, or deliberately ambiguous. */
  expectedOutcomeNote: string;
}

export interface ResearchQuestionRecord {
  researchQuestionId: string;
  /** The question in one short sentence, for a heading or a log line. Never sent to the model. */
  researchQuestionShort: string;
  /** Contains {{companyName}}, {{cityName}}, {{stateCode}} and {{websiteDomain}}. */
  researchQuestionTemplate: string;
  templatePlaceholderNames: string[];
  templatePlaceholderNote: string;
}

export interface QualificationRubricRecord {
  qualificationRubricId: string;
  /** Plain text, passed to the judge call as-is. Data, so it can be edited without a deploy. */
  qualificationRubricText: string;
  /** One sentence on what supportsNoOnlineOrdering true means in this rubric, for a learner reading the file. */
  verdictMeaning: string;
  verdictFieldNames: string[];
}

// ---------------------------------------------------------------------------------------------
// Recorded search
// ---------------------------------------------------------------------------------------------

export interface RecordedSearchResult {
  resultUrl: string;
  resultTitle: string;
  resultSnippet: string;
  /** Feed this to fetchRecordedPageTextById, or use resultUrl with fetchRecordedPageTextByUrl. */
  pageTextFixtureId: string;
}

export interface RecordedSearch {
  searchQueryText: string;
  searchResults: RecordedSearchResult[];
}

// ---------------------------------------------------------------------------------------------
// Recorded page text
// ---------------------------------------------------------------------------------------------

export interface RecordedPageText {
  pageUrl: string;
  pageTextFixtureId: string;
  companyId: CompanyId;
  /** Markdown, already truncated to MAX_RECORDED_PAGE_TEXT_CHARACTERS. */
  pageTextMarkdown: string;
}

/** The cap the live Firecrawl-backed tool applies in the artifact pages; recordings honour it too. */
export const MAX_RECORDED_PAGE_TEXT_CHARACTERS = 20_000;

// ---------------------------------------------------------------------------------------------
// Recorded model turns
// ---------------------------------------------------------------------------------------------

export interface RecordedToolCall {
  /** Unique within its conversation. Builds echo it back on the tool result. */
  toolCallId: string;
  /** Either "searchTheWeb" or "fetchPageText" in the current fixture set. */
  toolName: 'searchTheWeb' | 'fetchPageText';
  /** searchTheWeb: { searchQueryText }. fetchPageText: { pageUrl }. */
  toolArguments: Record<string, string>;
}

export interface RecordedFinalAnswer {
  answerKind: 'text' | 'json';
  /** Present when answerKind is "text". */
  answerText?: string;
  /** Present when answerKind is "json". Rung 3 stores the verdict object here. */
  answerJson?: Record<string, unknown>;
}

export interface RecordedModelTurn {
  /** 0-based, and always contiguous. */
  turnIndex: number;
  /** What the model said on this turn, for the transcript a learner reads. */
  assistantText: string;
  /** Empty on the final turn; non-empty on every other turn. */
  toolCallsRequested: RecordedToolCall[];
  stopReason: 'tool_calls' | 'final_answer';
  /** Present only on the turn whose stopReason is "final_answer". */
  finalAnswer?: RecordedFinalAnswer;
}

export interface RecordedConversation {
  fixtureVersion: string;
  rungId: RungId;
  companyId: CompanyId;
  conversationId: string;
  promptSummary: string;
  /** One paragraph explaining what this rung teaches; the builds may print it. */
  teachingNote: string;
  modelTurns: RecordedModelTurn[];
}

// ---------------------------------------------------------------------------------------------
// Expected verdicts
// ---------------------------------------------------------------------------------------------

export interface QualificationVerdict {
  supportsNoOnlineOrdering: boolean;
  researchReason: string;
  sourceUrls: string[];
}

export interface ExpectedVerdictRecord {
  fixtureVersion: string;
  companyId: CompanyId;
  qualificationRubricId: string;
  expectedVerdict: QualificationVerdict;
  /** Substrings every build's rung 2 research report must contain, verbatim and case-sensitive. */
  expectedResearchFactSubstrings: string[];
}

// ---------------------------------------------------------------------------------------------
// The replay session: what a build actually drives
// ---------------------------------------------------------------------------------------------

/**
 * A replay session hands back one recorded turn at a time, in order, for one rung and one company.
 * Each build wraps this in whatever its own SDK expects (a mock language model, a stub client, a
 * fake async generator) — the wrapping lives in the build folder, not here.
 */
export interface ReplayModelSession {
  readonly conversationId: string;
  readonly rungId: RungId;
  readonly companyId: CompanyId;
  readonly teachingNote: string;
  /** Total recorded turns, so a build can assert it consumed all of them. */
  readonly turnCount: number;
  /** How many turns have been handed out so far. */
  readonly turnsTaken: number;
  /** Next recorded turn. Throws ReplayExhaustedError when the recording runs out. */
  takeNextTurn(): RecordedModelTurn;
  /** True when every recorded turn has been handed out. */
  isFinished(): boolean;
}

/** Thrown when a build asks for a recording that was never recorded. */
export class ReplayFixtureMissingError extends Error {
  readonly missingFixtureDescription: string;
  constructor(missingFixtureDescription: string) {
    super(
      `No recording for ${missingFixtureDescription}. Offline mode never invents data. ` +
        'Either add the recording under shared/fixtures/, or change the build to ask for one that exists.',
    );
    this.name = 'ReplayFixtureMissingError';
    this.missingFixtureDescription = missingFixtureDescription;
  }
}

/** Thrown when a build asks for more model turns than were recorded. */
export class ReplayExhaustedError extends Error {
  constructor(conversationId: string, turnCount: number) {
    super(
      `Replay conversation ${conversationId} has only ${turnCount} recorded turns and the build asked for another one. ` +
        'The build is looping more times than the recording expects.',
    );
    this.name = 'ReplayExhaustedError';
  }
}
