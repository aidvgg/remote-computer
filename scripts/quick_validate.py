#!/usr/bin/env python3
"""Minimal metadata validator vendored from the skill-creator workflow."""

import re
import sys
from pathlib import Path
from typing import cast

import yaml

MAX_SKILL_NAME_LENGTH = 64


def validate_skill(skill_path: str | Path) -> tuple[bool, str]:
    skill_dir = Path(skill_path)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"
    content = skill_md.read_text()
    if not content.startswith("---"):
        return False, "No YAML frontmatter found"
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format"
    try:
        loaded: object = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return False, f"Invalid YAML in frontmatter: {exc}"
    if not isinstance(loaded, dict):
        return False, "Frontmatter must be a YAML dictionary"
    frontmatter = cast(dict[str, object], loaded)
    if set(frontmatter) != {"name", "description"}:
        return False, "Frontmatter must contain only name and description"
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        return False, "Name must be lowercase hyphen-case"
    if len(name) > MAX_SKILL_NAME_LENGTH:
        return False, f"Name exceeds {MAX_SKILL_NAME_LENGTH} characters"
    if not isinstance(description, str) or not description.strip():
        return False, "Description must be a non-empty string"
    if len(description) > 1024 or "<" in description or ">" in description:
        return False, "Description is too long or contains angle brackets"
    return True, "Skill is valid!"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python quick_validate.py <skill_directory>")
        raise SystemExit(1)
    valid, message = validate_skill(sys.argv[1])
    print(message)
    raise SystemExit(0 if valid else 1)
