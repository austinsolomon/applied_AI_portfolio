// Skills: specialized knowledge kept out of the system prompt until needed
// (progressive disclosure). Each skill is a folder with a SKILL.md file. The
// prompt lists only each skill's name and one-line description; the agent
// loads the full text with the loadSkill tool when a task needs it.
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

export interface Skill {
  name: string; // the folder name
  description: string; // from the SKILL.md frontmatter
  path: string; // full path to SKILL.md
}

// Reads `description:` from the frontmatter block between the opening and
// closing `---` lines. No library needed for one field.
function parseFrontmatter(md: string): { description?: string } {
  if (!md.startsWith("---")) return {};
  const end = md.indexOf("\n---", 3);
  if (end < 0) return {};
  const descLine = md.slice(3, end).split("\n").find((l) => l.startsWith("description:"));
  return { description: descLine?.replace("description:", "").trim().replace(/^['"]|['"]$/g, "") };
}

// Scans each directory for <dir>/<name>/SKILL.md. Directories are checked in
// order and the first skill with a given name wins, so project skills
// (listed first) override global ones with the same name.
export function discoverSkills(dirs: string[]): Skill[] {
  const skills: Skill[] = [];
  const seen = new Set<string>();
  for (const dir of dirs) {
    if (!existsSync(dir)) continue;
    for (const entry of readdirSync(dir)) {
      const path = join(dir, entry, "SKILL.md");
      if (!existsSync(path) || seen.has(entry)) continue;
      seen.add(entry);
      const { description } = parseFrontmatter(readFileSync(path, "utf-8"));
      skills.push({ name: entry, description: description ?? "(no description)", path });
    }
  }
  return skills;
}
