"""Skill: read a file from the agent's root directory."""

SKILL_NAME = "read_file"
SKILL_VERSION = "0.1.0"
SKILL_DESCRIPTION = (
    "Read the text content of a file relative to the agent workspace root. "
    "Returns the file contents as a string."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Relative path to the file, e.g. 'notes/todo.txt'",
        }
    },
    "required": ["path"],
}


def execute(path: str) -> str:
    import os
    from pathlib import Path

    safe_path = Path(path).resolve()
    root = Path(os.getcwd()).resolve()
    if not str(safe_path).startswith(str(root)):
        return "Error: path is outside the workspace root"
    if not safe_path.exists():
        return f"Error: file not found — {path}"
    if safe_path.stat().st_size > 1_000_000:
        return "Error: file is too large to read (>1MB)"
    return safe_path.read_text(errors="replace")
