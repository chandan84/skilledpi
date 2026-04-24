"""Tests for the skill loader — verifies discovery, schema, and execution."""

import asyncio
import textwrap
from pathlib import Path

import pytest

from chibu.agent.skill_loader import load_skills


@pytest.fixture()
def skill_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".pi" / "skills"
    d.mkdir(parents=True)
    return d


def _write_skill(d: Path, name: str, body: str) -> None:
    (d / f"{name}.py").write_text(textwrap.dedent(body))


def test_load_skills_discovers_all_py_files(skill_dir):
    for i in range(3):
        _write_skill(
            skill_dir,
            f"skill_{i}",
            f"""\
            SKILL_NAME = "skill_{i}"
            SKILL_DESCRIPTION = "Skill number {i}"
            SKILL_VERSION = "1.0.0"
            INPUT_SCHEMA = {{"type": "object", "properties": {{"x": {{"type": "integer"}}}}, "required": ["x"]}}

            def execute(x: int) -> int:
                return x * {i + 1}
            """,
        )
    skills = load_skills(skill_dir.parent)
    assert set(skills.keys()) == {"skill_0", "skill_1", "skill_2"}


def test_skill_has_correct_anthropic_tool_format(skill_dir):
    _write_skill(
        skill_dir,
        "adder",
        """\
        SKILL_NAME = "adder"
        SKILL_DESCRIPTION = "Add two integers"
        INPUT_SCHEMA = {
            "type": "object",
            "properties": {
                "a": {"type": "integer", "description": "First operand"},
                "b": {"type": "integer", "description": "Second operand"},
            },
            "required": ["a", "b"],
        }

        def execute(a: int, b: int) -> int:
            return a + b
        """,
    )
    skills = load_skills(skill_dir.parent)
    tool = skills["adder"].to_anthropic_tool()
    assert tool["name"] == "adder"
    assert "description" in tool
    assert tool["input_schema"]["type"] == "object"
    assert "a" in tool["input_schema"]["properties"]
    assert "b" in tool["input_schema"]["properties"]


def test_skill_execution_sync(skill_dir):
    _write_skill(
        skill_dir,
        "multiplier",
        """\
        SKILL_NAME = "multiplier"
        SKILL_DESCRIPTION = "Multiply two numbers"
        INPUT_SCHEMA = {"type": "object", "properties": {}, "required": []}

        def execute(x: float, y: float) -> float:
            return x * y
        """,
    )
    skills = load_skills(skill_dir.parent)
    result = asyncio.run(skills["multiplier"].execute(x=6.0, y=7.0))
    assert result == pytest.approx(42.0)


def test_skill_execution_async(skill_dir):
    _write_skill(
        skill_dir,
        "async_hello",
        """\
        SKILL_NAME = "async_hello"
        SKILL_DESCRIPTION = "Async greeting"
        INPUT_SCHEMA = {"type": "object", "properties": {}, "required": []}

        async def execute(name: str = "world") -> str:
            import asyncio
            await asyncio.sleep(0)
            return f"hello, {name}"
        """,
    )
    skills = load_skills(skill_dir.parent)
    result = asyncio.run(skills["async_hello"].execute(name="chibu"))
    assert result == "hello, chibu"


def test_skill_missing_execute_is_skipped(skill_dir):
    _write_skill(
        skill_dir,
        "broken",
        """\
        SKILL_NAME = "broken"
        SKILL_DESCRIPTION = "Has no execute"
        INPUT_SCHEMA = {"type": "object", "properties": {}, "required": []}
        # intentionally no execute()
        """,
    )
    skills = load_skills(skill_dir.parent)
    assert "broken" not in skills
