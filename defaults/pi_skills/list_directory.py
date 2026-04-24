"""Skill: list files and directories in the agent workspace."""

SKILL_NAME = "list_directory"
SKILL_VERSION = "0.1.0"
SKILL_DESCRIPTION = (
    "List files and subdirectories at a given path relative to the agent workspace. "
    "Returns a JSON array of entries."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Relative directory path; use '.' for workspace root",
        },
        "recursive": {
            "type": "boolean",
            "description": "If true, list all files recursively",
        },
    },
    "required": ["path"],
}


def execute(path: str = ".", recursive: bool = False) -> list:
    import os
    from pathlib import Path

    target = Path(path).resolve()
    root = Path(os.getcwd()).resolve()
    if not str(target).startswith(str(root)):
        return ["Error: path is outside the workspace root"]

    if not target.exists():
        return [f"Error: directory not found — {path}"]

    if recursive:
        entries = [
            str(p.relative_to(root))
            for p in sorted(target.rglob("*"))
            if not any(part.startswith(".") for part in p.parts)
        ]
    else:
        entries = sorted(
            e.name + ("/" if (target / e.name).is_dir() else "")
            for e in target.iterdir()
        )

    return entries[:500]  # safety cap
