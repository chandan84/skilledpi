"""Agent filesystem bootstrapper — creates and validates the .pi folder layout."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

DEFAULTS_DIR = Path(__file__).parent.parent.parent / "defaults"

PI_SUBDIRS = ["skills", "packages", "extensions"]


def bootstrap_agent_root(root: Path, agent_id: str, agent_name: str) -> None:
    """Create the full agent root directory including .pi structure."""
    root.mkdir(parents=True, exist_ok=True)

    pi = root / ".pi"
    for subdir in PI_SUBDIRS:
        (pi / subdir).mkdir(parents=True, exist_ok=True)

    _copy_defaults(root, pi)
    _write_agent_marker(root, agent_id, agent_name)


def _copy_defaults(root: Path, pi: Path) -> None:
    models_src = DEFAULTS_DIR / "models.json"
    if models_src.exists():
        shutil.copy2(models_src, root / "models.json")

    config_src = DEFAULTS_DIR / "config.yaml"
    if config_src.exists():
        shutil.copy2(config_src, root / "config.yaml")
        shutil.copy2(config_src, pi / "config.yaml")

    skills_src = DEFAULTS_DIR / "pi_skills"
    if skills_src.exists():
        for skill_file in skills_src.glob("*.py"):
            shutil.copy2(skill_file, pi / "skills" / skill_file.name)

    ext_src = DEFAULTS_DIR / "pi_extensions"
    if ext_src.exists():
        for ext_file in ext_src.glob("*.py"):
            shutil.copy2(ext_file, pi / "extensions" / ext_file.name)


def _write_agent_marker(root: Path, agent_id: str, agent_name: str) -> None:
    marker = root / ".pi" / "agent.json"
    marker.write_text(
        json.dumps({"agent_id": agent_id, "name": agent_name}, indent=2)
    )


def load_models(root: Path) -> list[dict]:
    models_file = root / "models.json"
    if not models_file.exists():
        return []
    data = json.loads(models_file.read_text())
    return data.get("models", [])


def load_config(root: Path) -> dict:
    import yaml

    config_file = root / "config.yaml"
    if not config_file.exists():
        return {}
    return yaml.safe_load(config_file.read_text()) or {}


def get_default_model(models: list[dict]) -> str | None:
    for m in models:
        if m.get("default"):
            return m["id"]
    return models[0]["id"] if models else None
