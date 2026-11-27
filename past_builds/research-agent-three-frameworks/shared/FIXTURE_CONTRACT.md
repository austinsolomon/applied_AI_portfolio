# The fixture contract

This document is the agreement between the shared recordings in `shared/` and the three builds that
replay them: `vercel_ai_sdk/`, `claude_agent_sdk/` and `openai_sdk/`.

If you are building one of those three folders, you can build the whole thing from this page. You do
not need to read the fixture files, and you do not need to ask whoever wrote them anything.

**Rule for changes.** The fixtures and this document change together, in one edit. If you need a
shape that is not here, say so rather than quietly writing a second shape.

Current `fixtureVersion`: **2.0.0**. Every fixture file declares it. The validator refuses a file that
declares anything else. Version 2.0.0 replaced the four local-services companies of 1.0.0 with four
Chicago bakeries and replaced the question and the rubric; no record shape changed.

## 0. Names to copy exactly

Every file that belongs to one company is named after its `companyId`. The rule for a `companyId` is
**the bakery's name in lower case, with every run of spaces or punctuation turned into one
underscore** (`&` is spelled `and`). Domains use hyphens and drop the word "bakery" where the table
says so, which means you copy them from here and never derive them.

| `companyId` | `companyName` | `websiteDomain` | Expected `supportsNoOnlineOrdering` |
|---|---|---|---|
| `bluebird_bakery` | Bluebird Bakery | `bluebird-bakery.example` | `true` |
| `oak_street_bakes` | Oak Street Bakes | `oak-street-bakes.example` | `false` |
| `rye_and_rosemary_bakery` | Rye & Rosemary Bakery | `rye-and-rosemary.example` | `true` |
| `dos_rios_panaderia` | Dos Rios Panaderia | `dos-rios-panaderia.example` | `false` |

That is also the order of `companies.json`. All four are in Chicago, IL.

The recorded pages, with the exact URL, file and `pageTextFixtureId` of each (13 pages):

| `pageUrl` | File under `recorded_page_texts/` | `pageTextFixtureId` |
|---|---|---|
| `https://bluebird-bakery.example/` | `bluebird_bakery/home.md` | `bluebird_bakery/home` |
| `https://bluebird-bakery.example/order` | `bluebird_bakery/order.md` | `bluebird_bakery/order` |
| `https://bluebird-bakery.example/about` | `bluebird_bakery/about.md` | `bluebird_bakery/about` |
| `https://oak-street-bakes.example/` | `oak_street_bakes/home.md` | `oak_street_bakes/home` |
| `https://oak-street-bakes.example/order` | `oak_street_bakes/order.md` | `oak_street_bakes/order` |
| `https://oak-street-bakes.example/about` | `oak_street_bakes/about.md` | `oak_street_bakes/about` |
| `https://rye-and-rosemary.example/` | `rye_and_rosemary_bakery/home.md` | `rye_and_rosemary_bakery/home` |
| `https://rye-and-rosemary.example/order` | `rye_and_rosemary_bakery/order.md` | `rye_and_rosemary_bakery/order` |
| `https://rye-and-rosemary.example/about` | `rye_and_rosemary_bakery/about.md` | `rye_and_rosemary_bakery/about` |
| `https://dos-rios-panaderia.example/` | `dos_rios_panaderia/home.md` | `dos_rios_panaderia/home` |
| `https://dos-rios-panaderia.example/menu` | `dos_rios_panaderia/menu.md` | `dos_rios_panaderia/menu` |
| `https://dos-rios-panaderia.example/about` | `dos_rios_panaderia/about.md` | `dos_rios_panaderia/about` |
| `https://chicago-delivery-directory.example/stores/dos-rios-panaderia` | `dos_rios_panaderia/delivery_directory_listing.md` | `dos_rios_panaderia/delivery_directory_listing` |

The last row is the one page in the set that is **not** on the bakery's own site: a stale outside
delivery listing that only the narrow Dos Rios search turns up. It is filed under
`dos_rios_panaderia/` because that is the company it concerns.

The two recorded search queries per company, verbatim:

| `companyId` | Broad query (rung 2, turn 0) | Narrow query (rung 2, follow-up) |
|---|---|---|
| `bluebird_bakery` | `Bluebird Bakery Chicago IL online orders locations owner` | `Bluebird Bakery Chicago order online` |
| `oak_street_bakes` | `Oak Street Bakes Chicago IL online orders locations owner` | `Oak Street Bakes Chicago order online` |
| `rye_and_rosemary_bakery` | `Rye & Rosemary Bakery Chicago IL online orders locations owner` | `Rye & Rosemary Bakery Chicago order ahead online` |
| `dos_rios_panaderia` | `Dos Rios Panaderia Chicago IL online orders locations owner` | `Dos Rios Panaderia Chicago delivery order online` |

---

## 1. The task, one paragraph

Given one bakery and one research question — *does this bakery already take online orders?* — find
and read evidence on the web, then return a verdict: `supportsNoOnlineOrdering` (a boolean),
`researchReason` (one sentence) and `sourceUrls` (the pages the verdict rests on). All three
builds do exactly this, over exactly these four bakeries, using exactly this rubric — that is the only
way a four-way comparison means anything.

`supportsNoOnlineOrdering: true` means the evidence supports no online ordering. False means contradicted or unproven. This agent is part of Enrich, not Qualify: a person inspects the cited evidence, confirms it into the record, and Repo 1's Qualify then runs its four fit checks once, on the completed record. The ordering evidence alone never qualifies the lead by itself.

The code keeps the word "company" (`companyId`, `CompanyRecord`, `companies.json`) because the agent
would work on any kind of business. In this fixture set every company is a bakery.

## 2. The four companies

From `shared/fixtures/companies.json`.

| `companyId` | Name | Domain | What it is for |
|---|---|---|---|
| `bluebird_bakery` | Bluebird Bakery, Chicago IL | `bluebird-bakery.example` | Clearly settles `true`: one shop, two named co-owners, and an order page that says "call us or stop by the counter. We do not take orders online." |
| `oak_street_bakes` | Oak Street Bakes, Chicago IL | `oak-street-bakes.example` | Clearly settles `false`: an Order Online button that opens a working store with a cart, pickup times and card payment |
| `rye_and_rosemary_bakery` | Rye & Rosemary Bakery, Chicago IL | `rye-and-rosemary.example` | **Deliberately unclear, settled by reading further**: the home page has an Order Ahead button, and the page behind it only gives a phone number and an emailed order form, with payment at pickup |
| `dos_rios_panaderia` | Dos Rios Panaderia, Chicago IL | `dos-rios-panaderia.example` | **Deliberately unclear, stays unproven**: its own pages never say how orders are taken, and the only outside listing is undated and says the store is not accepting orders |

Every bakery is invented and every domain ends in `.example`, a name reserved by the standards
bodies for documentation, so no fixture can ever point at a real business. The validator enforces it,
for the bakeries' own sites and for the one outside listing.

One clearly settles `true`, one clearly settles `false`, and two are deliberately unclear. The two unclear ones
fail in opposite directions, on purpose:

- `rye_and_rosemary_bakery` punishes an agent that **stops too early**. Read only the home page and
  the Order Ahead button looks like online ordering, so the verdict comes out `false`. Read the order
  page and it is `true`. Rung 1 (one page) gets this bakery wrong and rung 2 (the loop) gets it right;
  that contrast is the reason the bakery is in the set.
- `dos_rios_panaderia` punishes an agent that **guesses**. No amount of reading proves the ordering method, so the
  rubric's *false when unproven* clause decides it. A build that returns `supportsNoOnlineOrdering: true` for
  `dos_rios_panaderia` has a bug, not an opinion.

Record shape:

```ts
interface CompanyRecord {
  companyId: string;          // lower_snake_case, matches every filename that mentions this company
  companyName: string;
  websiteDomain: string;      // always ends ".example"
  cityName: string;
  stateCode: string;
  industryLabel: string;
  expectedOutcomeNote: string; // why this company is in the set
}
```

## 3. The research question

`shared/fixtures/research_question.json` holds one template with `{{companyName}}`, `{{cityName}}`,
`{{stateCode}}` and `{{websiteDomain}}` placeholders. Call
`buildResearchQuestionForCompany(company)` to fill it. Do not write your own prompt text for the
research step; all three builds must send the same question.

Filled, for Bluebird Bakery, it reads:

> Bakery: Bluebird Bakery, Chicago IL (bluebird-bakery.example). Does this bakery already take online
> orders? Find: how customers place an order today, how many locations it runs, and who owns or
> manages it. Cite the page you took each fact from.

The file also carries `researchQuestionId` (`takes_online_orders`) and `researchQuestionShort`
("Does this bakery already take online orders?"), which is for headings and log lines and is never
sent to the model.

## 4. The rubric

`shared/fixtures/qualification_rubric.json` holds `qualificationRubricText`, plain text, with
`qualificationRubricId` `bakery_ordering_evidence_v2`. Three numbered rules:

1. **Independent.** The bakery runs between 1 and 3 locations, as a stated number or a countable list
   of addresses.
2. **No online ordering today.** Its own pages show orders are taken by phone, in person, by email or
   on a paper form. A working cart or checkout, or a link to an online ordering platform, fails the
   rule. Pages that never say how orders are taken leave it unproven.
3. **A person to write to.** An owner or manager is named on the bakery's own pages.

Then the instruction to return `false` when any rule is unproven, and to say which. Pass the text to
the judge call as-is. It is data on purpose: a learner can edit the rubric and re-run only rung 3.
`verdictMeaning` is one sentence for a human reader; `verdictFieldNames` lists the three fields.

## 5. Recorded search results

`shared/fixtures/recorded_search_results/<companyId>.json`. Each file holds `recordedSearches`, an
array of `{ searchQueryText, searchResults }`. Each result is:

```ts
interface RecordedSearchResult {
  resultUrl: string;          // always has a recorded page behind it
  resultTitle: string;
  resultSnippet: string;
  pageTextFixtureId: string;  // e.g. "bluebird_bakery/order"
}
```

Call it with `searchRecordedWeb(companyId, searchQueryText)`. Matching normalizes case and
punctuation, then falls back to word overlap, so a query whose wording drifted by a word still
replays. A query with nothing in common with any recording throws `ReplayFixtureMissingError` — the
replay layer never invents a result.

Every company has exactly two recorded queries, in this order, with the exact text in section 0: a
broad one matching the research question, and a narrow ordering-focused one.

- The **broad** search returns the bakery's three own pages, home page first.
- The **narrow** search returns the order page first (for Dos Rios, which has no order page, the
  stale outside listing first and then the menu page). The outside listing appears in no other search.

## 6. Recorded page texts

`shared/fixtures/recorded_page_texts/<companyId>/<page>.md` — three pages of the bakery's own site per
company (`home`, `order`, `about`; Dos Rios has `menu` in place of `order`), 300-800 words each,
written as the bakery's own site would read, each starting with its `Source URL:` line. Dos Rios has a
fourth file, the outside delivery listing, which may be as short as 80 words because a dead listing
is short. Section 0 lists all 13.

The sentences a verdict rests on are fixed, because the shared tests look for them:

| Page | Must contain, verbatim |
|---|---|
| `bluebird_bakery/order` | `call us or stop by` |
| `rye_and_rosemary_bakery/home` | `Order Ahead` |
| `rye_and_rosemary_bakery/order` | `emailing our order form` and `Pay when you pick up` |
| `dos_rios_panaderia/delivery_directory_listing` | `not currently accepting orders` |

`page_index.json` in that folder maps every URL to its file; a page not in the index is unreachable,
and the validator fails if a `.md` file on disk is missing from it.

Call it with `fetchRecordedPageTextByUrl(pageUrl)` or `fetchRecordedPageTextById(pageTextFixtureId)`.
Both return:

```ts
interface RecordedPageText {
  pageUrl: string;
  pageTextFixtureId: string;
  companyId: string;
  pageTextMarkdown: string;   // truncated at MAX_RECORDED_PAGE_TEXT_CHARACTERS (20,000)
}
```

URL matching ignores a trailing slash and the `http`/`https` prefix. Anything else throws
`ReplayFixtureMissingError`.

## 7. Recorded model responses — the important one

`shared/fixtures/recorded_model_responses/<rungId>/<companyId>.json`, one file per rung per company,
16 files in all. The four rung ids are:

| `rungId` | What it records | Tool calls |
|---|---|---|
| `rung0_one_model_call` | One call, no tools. The model admits it cannot answer and cites nothing. | none |
| `rung1_one_tool` | The model asks for the home page once, then answers from it. For Rye & Rosemary that answer is wrong, which is the point. | 1 `fetchPageText` |
| `rung2_agent_loop` | Search, read three pages at once, one narrow follow-up search, then a written research report. Dos Rios takes one more turn, to read the outside listing the narrow search found. | 4 turns, 5 calls (Dos Rios: 5 turns, 6 calls) |
| `rung3_judge_verdict` | Rubric plus the stored report, no tools, a typed verdict. | none |

The shape is **provider-neutral on purpose**. It is not an OpenAI response, not an AI SDK result and
not a Claude Agent SDK message. Each build converts it into whatever its own SDK expects; that
conversion lives in the build folder.

```ts
interface RecordedConversation {
  fixtureVersion: string;
  rungId: RungId;
  companyId: string;
  conversationId: string;     // "<rungId>:<companyId>"
  promptSummary: string;      // what was sent, for a learner reading the file
  teachingNote: string;       // one paragraph on what this rung teaches; builds may print it
  modelTurns: RecordedModelTurn[];
}

interface RecordedModelTurn {
  turnIndex: number;                    // 0-based, always contiguous
  assistantText: string;                // what the model said this turn
  toolCallsRequested: RecordedToolCall[]; // empty on the last turn, non-empty on every other
  stopReason: 'tool_calls' | 'final_answer';
  finalAnswer?: RecordedFinalAnswer;    // present only on the final turn
}

interface RecordedToolCall {
  toolCallId: string;                   // unique in its conversation; echo it on the tool result
  toolName: 'searchTheWeb' | 'fetchPageText';
  toolArguments: Record<string, string>;
  //   searchTheWeb  -> { searchQueryText: string }
  //   fetchPageText -> { pageUrl: string }
}

interface RecordedFinalAnswer {
  answerKind: 'text' | 'json';
  answerText?: string;                  // when answerKind === 'text'
  answerJson?: Record<string, unknown>; // when answerKind === 'json' (rung 3's verdict)
}
```

Invariants the validator enforces, so you may rely on them:

1. `turnIndex` runs 0, 1, 2, … with no gaps.
2. Exactly the last turn has `stopReason: 'final_answer'` and a `finalAnswer`; it requests no tools.
3. Every earlier turn has `stopReason: 'tool_calls'` and at least one tool call.
4. Every `fetchPageText` URL exists in `page_index.json`.
5. Every `searchTheWeb` query exists verbatim in that company's recorded searches.
6. Rungs 0 and 3 request no tools at all.
7. Rung 3's `answerJson` equals the company's `expected_verdicts/<companyId>.json` exactly.
8. Rung 2's final `answerText` contains every one of the company's
   `expectedResearchFactSubstrings`, verbatim and case-sensitive.

The rung 2 report is plain text under these headings, in this order: `HOW ORDERS ARE TAKEN:`,
`LOCATIONS:`, `OWNER OR MANAGER:`, `CONFIDENCE:`, each fact followed by a `Source:` line.

### Driving it

```ts
import { openReplayModelSession } from '../../shared/replay/index.ts';

const replaySession = openReplayModelSession('rung2_agent_loop', 'bluebird_bakery');
while (!replaySession.isFinished()) {
  const modelTurn = replaySession.takeNextTurn();
  // run modelTurn.toolCallsRequested through your build's tools, or read modelTurn.finalAnswer
}
```

`takeNextTurn()` hands back one turn per model call your build would have made. Ask for one more than
was recorded and it throws `ReplayExhaustedError` — which is the signal that your loop is running more
times than the recording expects, not a reason to catch and continue.

## 8. Expected verdicts

`shared/fixtures/expected_verdicts/<companyId>.json`. This is what each build's rung 3 test asserts
against.

```ts
interface ExpectedVerdictRecord {
  fixtureVersion: string;
  companyId: string;
  qualificationRubricId: string;
  expectedVerdict: {
    supportsNoOnlineOrdering: boolean;
    researchReason: string;   // one sentence
    sourceUrls: string[];          // every one has a recorded page behind it
  };
  expectedResearchFactSubstrings: string[]; // the rung 2 report must contain each, case-sensitive
}
```

`qualificationRubricId` is `bakery_ordering_evidence_v2` in all four; the validator checks it
against the rubric file.

Expected results: `bluebird_bakery` true, `rye_and_rosemary_bakery` true, `oak_street_bakes` false,
`dos_rios_panaderia` false. Two true, two false; the validator insists on exactly that split.

| `companyId` | `researchReason` | `expectedResearchFactSubstrings` |
|---|---|---|
| `bluebird_bakery` | The ordering page says cake orders are taken by phone or in person only, at the bakery's single Lincoln Square shop run by its two named owners. | `call us or stop by`, `Hannah Kowalski`, `one location` |
| `oak_street_bakes` | The order page is a working online store with a cart, pickup times and card payment, so the bakery already takes online orders. | `add to cart`, `pay by card`, `Dale Pruitt` |
| `rye_and_rosemary_bakery` | The Order Ahead page only gives a phone number and an emailed order form with payment at pickup, so the single-shop bakery does not take online orders. | `emailing our order form`, `Pay when you pick up`, `Sofia Marchetti` |
| `dos_rios_panaderia` | The ordering method is unproven: the bakery's own pages never say how orders are taken, and the only outside listing is undated and inactive. | `not currently accepting orders`, `Elena Villarreal`, `never say how orders are taken` |

## 9. What your build imports

One path, one module:

```ts
import {
  buildResearchQuestionForCompany,
  fetchRecordedPageTextByUrl,
  loadAllCompanies,
  loadCompanyById,
  loadExpectedVerdict,
  loadQualificationRubric,
  openReplayModelSession,
  searchRecordedWeb,
} from '../../shared/replay/index.ts';

import type { RecordedModelTurn, QualificationVerdict } from '../../shared/replay/index.ts';
```

The number of `../` steps depends on how deep your file sits. `shared/` is a sibling of the three
build folders, so from `vercel_ai_sdk/lib/judge-lead.ts` it is `'../../shared/replay/index.ts'`, and
from a file at the build folder's own root it is `'../shared/replay/index.ts'`.

Plain relative imports. No workspaces, no path aliases, no build step — the modules are TypeScript
that Node runs directly with `--experimental-strip-types`, so they contain no enums, no namespaces and
no decorators. Do not import any file under `shared/` other than `shared/replay/index.ts`; everything
the contract promises is re-exported there.

## 10. What the shared layer will never do

- It will never reach the network. It reads files and nothing else.
- It will never invent a search result, a page or a model turn. A miss throws a named error.
- It will never know which SDK you are using. Every SDK-shaped adapter lives in your build folder.
- It will never need an API key, an environment variable or an `npm install`. `shared/` has zero
  dependencies.

## 11. Checking the fixtures

```
cd shared
npm run validate   # every consistency check, one line per problem, exit code 1 on failure
npm test           # the same checks plus tests of the replay modules themselves
```

Both are offline and take under a second. Run them before you start, so that a failure later is
provably yours and not the fixtures'.
