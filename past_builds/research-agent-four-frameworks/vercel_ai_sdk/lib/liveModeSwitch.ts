// Offline or live: one switch, checked in one place.
//
// Offline is the default and costs nothing. Live mode sends real requests to paid services and is
// only ever on when the learner sets RESEARCH_AGENT_LIVE_MODE=on themselves. An empty environment
// variable counts as unset, so a copied-but-unfilled .env cannot turn live mode on by accident.

const LIVE_MODE_ENVIRONMENT_VARIABLE_NAME = 'RESEARCH_AGENT_LIVE_MODE';

export function readEnvironmentValue(environmentVariableName: string): string | undefined {
  const rawValue = process.env[environmentVariableName];
  if (rawValue === undefined || rawValue.trim() === '') {
    return undefined;
  }
  return rawValue.trim();
}

/** True only when the learner explicitly asked for live mode. */
export function isLiveModeOn(): boolean {
  return readEnvironmentValue(LIVE_MODE_ENVIRONMENT_VARIABLE_NAME) === 'on';
}

/** Reads a key, or explains exactly which variable live mode is missing. */
export function requireLiveModeKey(environmentVariableName: string): string {
  const keyValue = readEnvironmentValue(environmentVariableName);
  if (keyValue === undefined) {
    throw new Error(
      `Live mode is on (${LIVE_MODE_ENVIRONMENT_VARIABLE_NAME}=on) but ${environmentVariableName} is not set. ` +
        'Set it, or unset the live-mode switch to go back to the free offline recordings.',
    );
  }
  return keyValue;
}

/** One line per run saying which path was taken, so nobody has to guess. */
export function announceSelectedMode(rungLabel: string): void {
  if (isLiveModeOn()) {
    console.log(`${rungLabel}: LIVE mode — real model and network calls, and they cost money.`);
    return;
  }
  console.log(`${rungLabel}: OFFLINE mode — replaying shared recordings. No API keys, no cost.`);
}
