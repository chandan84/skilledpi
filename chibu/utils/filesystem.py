"""Agent filesystem bootstrapper — creates workspace and .pi/ layout."""

from __future__ import annotations

import json
from pathlib import Path

PI_SUBDIRS = ["skills", "extensions", "packages"]


def bootstrap_agent_root(
    workspace: Path,
    agent_id: str,
    agent_name: str,
    chiboo: str = "",
) -> None:
    """Create workspace + .pi/ directory structure for a new agent."""
    workspace.mkdir(parents=True, exist_ok=True)

    pi = workspace / ".pi"
    for sub in PI_SUBDIRS:
        (pi / sub).mkdir(parents=True, exist_ok=True)

    # Agent identity marker consumed by pi extensions and skills
    (pi / "agent.json").write_text(
        json.dumps(
            {"agent_id": agent_id, "name": agent_name, "chiboo": chiboo},
            indent=2,
        )
    )

    # Project context file — pi injects this into every system prompt
    agents_md = workspace / "AGENTS.md"
    if not agents_md.exists():
        agents_md.write_text(
            f"# {agent_name}\n\n"
            f"You are **{agent_name}**, a Chibu pi agent in the **{chiboo}** chiboo.\n"
            f"Agent ID: `{agent_id}`\n"
        )
