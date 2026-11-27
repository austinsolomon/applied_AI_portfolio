// ILLUSTRATIVE - not run. Rung 5: the whole "/column reference" feature.
//
// Not imported by the runnable rungs, which take their prompt from the shared fixture contract's
// buildResearchQuestionForCompany() instead. See docs/rung_status.md.

export const fillTemplate = (promptTemplate: string, leadFields: Record<string, string>) =>
  promptTemplate.replace(/\{\{(\w+)\}\}/g, (wholePlaceholder, fieldName) => leadFields[fieldName] ?? '');
