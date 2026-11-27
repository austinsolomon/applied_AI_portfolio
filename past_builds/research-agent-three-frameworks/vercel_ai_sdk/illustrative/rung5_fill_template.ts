// ILLUSTRATIVE - not run
//
// Rung 5, second half — lib/fill-template.ts. The whole "reference another column" feature of a
// research grid is this one substitution: a column's prompt template says {{companyName}}, and
// each row fills it with its own value ("Bluebird Bakery", "Oak Street Bakes", ...).
//
// Not run here because nothing in this offline build stores rows: the four fixture bakeries come
// from shared/fixtures/companies.json instead, and the research question is filled by
// buildResearchQuestionForCompany().

export const fillTemplate = (promptTemplate: string, leadFields: Record<string, string>) =>
  promptTemplate.replace(
    /\{\{(\w+)\}\}/g,
    (wholePlaceholder, fieldName) => leadFields[fieldName] ?? '',
  );
