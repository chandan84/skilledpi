"""Dashboard — root page and summary API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chibu.control_plane.app import templates
from chibu.control_plane.deps import get_registry
from chibu.db.engine import get_session
from chibu.db.models import Agent, AgentGroup
from chibu.registry.agent_registry import AgentRegistry

router = APIRouter(tags=["dashboard"])
logger = logging.getLogger("chibu.control_plane.dashboard")


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    registry: AgentRegistry = Depends(get_registry),
):
    summary = await registry.dashboard_summary()
    groups = await registry.list_groups()
    agents = await registry.list_agents()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "summary": summary,
            "groups": groups,
            "agents": agents,
        },
    )


@router.get("/api/summary")
async def api_summary(
    registry: AgentRegistry = Depends(get_registry),
) -> dict:
    return await registry.dashboard_summary()


@router.get("/api/analytics")
async def api_analytics(
    session: AsyncSession = Depends(get_session),
) -> dict:
    status_rows = await session.execute(
        select(Agent.status, func.count().label("n")).group_by(Agent.status)
    )
    status_dist = {r.status: r.n for r in status_rows}

    chiboo_rows = await session.execute(
        select(AgentGroup.name, func.count(Agent.agent_id).label("n"))
        .join(Agent, Agent.group_id == AgentGroup.id, isouter=True)
        .group_by(AgentGroup.name)
    )
    agents_by_chiboo = [{"chiboo": r.name, "count": r.n} for r in chiboo_rows]

    return {
        "status_distribution": status_dist,
        "agents_by_chiboo": agents_by_chiboo,
    }
