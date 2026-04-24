"""Shared FastAPI dependencies."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from chibu.db.engine import get_session
from chibu.process.manager import AgentProcessManager
from chibu.registry.agent_registry import AgentRegistry


@lru_cache(maxsize=1)
def get_process_manager() -> AgentProcessManager:
    agents_dir = Path(os.getenv("CHIBU_AGENTS_DIR", "agents"))
    snapshot = Path(os.getenv("CHIBU_REGISTRY_SNAPSHOT", "chibu_registry.json"))
    return AgentProcessManager(agents_dir=agents_dir, registry_snapshot_path=snapshot)


async def get_registry(
    session: AsyncSession = Depends(get_session),
) -> AgentRegistry:
    return AgentRegistry(session)
