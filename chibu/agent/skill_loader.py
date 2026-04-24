"""Discovers and loads skill modules from .pi/skills/."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("chibu.skills")


class Skill:
    """Runtime representation of a loaded skill."""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict,
        execute_fn: Callable,
        version: str = "0.1.0",
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.execute_fn = execute_fn
        self.version = version

    def to_anthropic_tool(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    async def execute(self, **kwargs: Any) -> Any:
        import asyncio

        if asyncio.iscoroutinefunction(self.execute_fn):
            return await self.execute_fn(**kwargs)
        return self.execute_fn(**kwargs)


def load_skills(pi_dir: Path) -> dict[str, Skill]:
    """Load all *.py skill files from .pi/skills/ and return them by name."""
    skills_dir = pi_dir / "skills"
    if not skills_dir.exists():
        return {}

    skills: dict[str, Skill] = {}

    for py_file in sorted(skills_dir.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(
                f"chibu_skill_{py_file.stem}", py_file
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # Each skill module must expose SKILL_NAME, SKILL_DESCRIPTION,
            # INPUT_SCHEMA, and an execute() function.
            name = getattr(mod, "SKILL_NAME", py_file.stem)
            description = getattr(mod, "SKILL_DESCRIPTION", "No description.")
            schema = getattr(mod, "INPUT_SCHEMA", {"type": "object", "properties": {}})
            execute_fn = getattr(mod, "execute", None)
            version = getattr(mod, "SKILL_VERSION", "0.1.0")

            if execute_fn is None:
                logger.warning("Skill %s has no execute() — skipped", py_file.name)
                continue

            skill = Skill(name, description, schema, execute_fn, version)
            skills[name] = skill
            logger.debug("Loaded skill: %s v%s", name, version)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load skill %s: %s", py_file.name, exc)

    return skills
