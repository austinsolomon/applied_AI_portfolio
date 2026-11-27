// Command-line entry point for the fixture checks.
//
//   cd shared && npm run validate
//   node --experimental-strip-types shared/validation/runFixtureValidation.ts
//
// Exit code 0 = every check passed. Exit code 1 = one line printed per problem.

import { validateFixtures } from './validateFixtures.ts';

const foundProblems = validateFixtures();

if (foundProblems.length === 0) {
  console.log(
    'Fixture validation passed: every recording is present, shaped correctly and internally consistent.',
  );
} else {
  console.error(`Fixture validation found ${foundProblems.length} problem(s):`);
  for (const problem of foundProblems) {
    console.error(`  [${problem.problemArea}] ${problem.problemDescription}`);
  }
  process.exitCode = 1;
}
