"""Skill: write text content to a file in the agent's workspace."""

SKILL_NAME = "write_file"
SKILL_VERSION = "0.1.0"
SKILL_DESCRIPTION = (
    "Write or overwrite a file relative to the agent workspace root. "
    "Creates parent directories if they do not exist."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Relative path to the target file",
        },
        "content": {
            "type": "string",
            "description": "Text content to write",
        },
    },
    "required": ["path", "content"],
}


def execute(path: str, content: str) -> str:
    import os
    from pathlib import Path

    safe_path = Path(path).resolve()
    root = Path(os.getcwd()).resolve()
    if not str(safe_path).startswith(str(root)):
        return "Error: path is outside the workspace root"
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(content)
    return f"Written {len(content)} characters to {path}"
