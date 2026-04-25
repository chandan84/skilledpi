"""Chiboo (agent group) management endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from chibu.control_plane.deps import get_registry
from chibu.registry.agent_registry import AgentRegistry

router = APIRouter(tags=["chiboos"])
logger = logging.getLogger("chibu.control_plane.chiboos")


class CreateChibooRequest(BaseModel):
    name: str
    description: str = ""


@router.get("/")
async def list_chiboos(registry: AgentRegistry = Depends(get_registry)) -> list[dict]:
    groups = await registry.list_groups()
    return [_chiboo_dict(g) for g in groups]


@router.post("/")
async def create_chiboo(
    body: CreateChibooRequest,
    registry: AgentRegistry = Depends(get_registry),
) -> dict:
    group = await registry.get_or_create_group(body.name, body.description)
    return _chiboo_dict(group)


@router.delete("/{chiboo_name}")
async def delete_chiboo(
    chiboo_name: str,
    registry: AgentRegistry = Depends(get_registry),
) -> dict:
    group = await registry.get_group_by_name(chiboo_name)
    if not group:
        raise HTTPException(404, "Chiboo not found")

    agents = await registry.list_agents(group_id=group.id)
    if agents:
        raise HTTPException(409, "Remove all agents in this chiboo first")

    await registry.delete_group(group.id)
    return {"ok": True}


def _chiboo_dict(g) -> dict:
    return {
        "id": g.id,
        "name": g.name,
        "description": g.description,
        "agent_count": len(g.agents) if g.agents is not None else 0,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }
