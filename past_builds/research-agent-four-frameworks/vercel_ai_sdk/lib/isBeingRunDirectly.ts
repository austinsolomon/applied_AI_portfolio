// "Was this file started from the command line, or imported by another file?"
//
// Each rung file both exports a function (so the tests can call it) and prints a demonstration when
// you run it yourself. This tells the two apart, and works on every Node version this repo supports.

import { pathToFileURL } from 'node:url';

export function isBeingRunDirectly(moduleUrl: string): boolean {
  const startedScriptPath = process.argv[1];
  if (startedScriptPath === undefined) {
    return false;
  }
  return pathToFileURL(startedScriptPath).href === moduleUrl;
}
