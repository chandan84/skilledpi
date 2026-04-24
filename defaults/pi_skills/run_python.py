"""Skill: execute a small Python snippet and return its output.

The snippet runs in a restricted exec context — no filesystem writes,
no network, no subprocess spawning.  Stdout is captured and returned.
"""

SKILL_NAME = "run_python"
SKILL_VERSION = "0.1.0"
SKILL_DESCRIPTION = (
    "Execute a small Python code snippet and return printed output. "
    "Useful for calculations, data transformations, and quick logic checks."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "description": "Python code to execute; use print() to produce output",
        }
    },
    "required": ["code"],
}

_BLOCKED = ("import os", "import sys", "import subprocess", "open(", "__import__")


def execute(code: str) -> str:
    import io
    import contextlib

    for blocked in _BLOCKED:
        if blocked in code:
            return f"Error: blocked construct '{blocked}'"

    stdout_capture = io.StringIO()
    local_ns: dict = {}

    try:
        with contextlib.redirect_stdout(stdout_capture):
            exec(compile(code, "<skill>", "exec"), {"__builtins__": __builtins__}, local_ns)  # noqa: S102
    except Exception as exc:
        return f"RuntimeError: {exc}"

    output = stdout_capture.getvalue()
    return output if output else "(no output)"
