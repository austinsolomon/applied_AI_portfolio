// The one module the three builds import. Everything a build needs from the shared recordings is
// re-exported here, so a build folder only ever needs this one module. `shared/` is a sibling of the
// three build folders, so the number of `../` steps depends on how deep the importing file sits —
// from vercel_ai_sdk/lib/judge-lead.ts, for example:
//
//   import { openReplayModelSession, searchRecordedWeb } from '../../shared/replay/index.ts';
//   import type { RecordedModelTurn } from '../../shared/replay/index.ts';
//
// The full shapes are documented in shared/FIXTURE_CONTRACT.md.

export {
  buildResearchQuestionForCompany,
  listCompanyIdsWithExpectedVerdicts,
  loadAllCompanies,
  loadCompanyById,
  loadExpectedVerdict,
  loadQualificationRubric,
  loadResearchQuestion,
} from './fixtureCatalogue.ts';

export {
  listRecordedSearchCompanyIds,
  listRecordedSearchesForCompany,
  searchRecordedWeb,
} from './recordedSearchProvider.ts';

export {
  fetchRecordedPageTextById,
  fetchRecordedPageTextByUrl,
  listRecordedPages,
  listRecordedPagesForCompany,
} from './recordedPageTextProvider.ts';

export {
  listRecordedCompanyIdsForRung,
  listRecordedRungIds,
  loadRecordedConversation,
  openReplayModelSession,
} from './recordedModelTurnProvider.ts';

export {
  MAX_RECORDED_PAGE_TEXT_CHARACTERS,
  ReplayExhaustedError,
  ReplayFixtureMissingError,
} from './replayTypes.ts';

export type {
  CompanyId,
  CompanyRecord,
  ExpectedVerdictRecord,
  QualificationRubricRecord,
  QualificationVerdict,
  RecordedConversation,
  RecordedFinalAnswer,
  RecordedModelTurn,
  RecordedPageText,
  RecordedSearch,
  RecordedSearchResult,
  RecordedToolCall,
  ReplayModelSession,
  ResearchQuestionRecord,
  RungId,
} from './replayTypes.ts';

export {
  EXPECTED_FIXTURE_VERSION,
  expectedVerdictsFolderPath,
  fixturesFolderPath,
  sharedFolderPath,
} from './fixtureFileLocations.ts';
