"""Skills: specialized knowledge kept out of the system prompt until needed.

This is progressive disclosure. Each skill is a folder with a SKILL.md file.
The prompt lists only each skill's name and one-line description; the agent
loads the full text with the loadSkill tool when a task needs it.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    """One discovered skill: its folder name, description, and SKILL.md path."""

    name: str
    description: str
    path: Path


def parse_description(md: str) -> str | None:
    """Read `description:` from the frontmatter block between the `---` lines."""
    if not md.startswith("---"):
        return None
    end = md.find("\n---", 3)
    if end < 0:
        return None
    for line in md[3:end].splitlines():
        if line.startswith("description:"):
            return line.removeprefix("description:").strip().strip("'\"")
    return None


def discover_skills(dirs: list[Path]) -> list[Skill]:
    """Scan each directory for <dir>/<name>/SKILL.md.

    Directories are checked in order and the first skill with a given name
    wins, so project skills (listed first) override global ones.
    """
    skills: list[Skill] = []
    seen: set[str] = set()
    for directory in dirs:
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            path = entry / "SKILL.md"
            if entry.name in seen or not path.is_file():
                continue
            seen.add(entry.name)
            description = parse_description(path.read_text(encoding="utf-8"))
            skills.append(Skill(entry.name, description or "(no description)", path))
    return skills
