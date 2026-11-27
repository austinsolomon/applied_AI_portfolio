// ILLUSTRATIVE - not run
//
// Rung 5, the other half: filling {{companyName}}, {{cityName}}, {{stateCode}} and {{websiteDomain}}
// into a prompt template before the research call, so one column asks "Does this bakery already
// take online orders?" of every bakery row. Substitution needs no model. Shown here for the grid's
// sake; the runnable rungs build their question through buildResearchQuestionForCompany() in
// shared/replay instead.

// TypeScript: field expansion happens before research().
const fillTemplate = (template: string, fields: Record<string, string>) =>
  template.replace(/\{\{(\w+)\}\}/g, (_, key) => fields[key] ?? '');
