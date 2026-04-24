"""Agent group management endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from chibu.control_plane.deps import get_registry
from chibu.db.models import AgentGroup
from chibu.registry.agent_registry import AgentRegistry

router = APIRouter(tags=["groups"])
logger = logging.getLogger("chibu.control_plane.groups")


class CreateGroupRequest(BaseModel):
    name: str
    description: str = ""


@router.get("/")
async def list_groups(
    registry: AgentRegistry = Depends(get_registry),
) -> list[dict]:
    groups = await registry.list_groups()
    return [_group_dict(g) for g in groups]


@router.post("/")
async def create_group(
    body: CreateGroupRequest,
    registry: AgentRegistry = Depends(get_registry),
) -> dict:
    group = await registry.get_or_create_group(
        name=body.name, description=body.description
    )
    return _group_dict(group)


@router.delete("/{group_id}")
async def delete_group(
    group_id: str,
    registry: AgentRegistry = Depends(get_registry),
) -> dict:
    agents = await registry.list_agents(group_id=group_id)
    if agents:
        raise HTTPException(409, "Remove all agents in this group first")

    deleted = await registry.delete_group(group_id)
    if not deleted:
        raise HTTPException(404, "Group not found")

    return {"ok": True}


def _group_dict(g: AgentGroup) -> dict:
    return {
        "id": g.id,
        "name": g.name,
        "description": g.description,
        "agent_count": len(g.agents) if g.agents is not None else 0,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }
