"""Dashboard — root page and summary API."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from chibu.control_plane.app import templates
from chibu.control_plane.deps import get_registry
from chibu.db.engine import get_session
from chibu.db.models import Agent, AgentEvent, AgentGroup, LLMRequest, PerformanceMetric
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
    """Return time-series data for the dashboard charts."""
    since = datetime.now(timezone.utc) - timedelta(hours=24)

    # LLM requests per hour (last 24h)
    llm_rows = await session.execute(
        select(
            func.strftime("%Y-%m-%dT%H:00:00", LLMRequest.created_at).label("hour"),
            func.count().label("count"),
            func.sum(LLMRequest.prompt_tokens + LLMRequest.completion_tokens).label(
                "tokens"
            ),
        )
        .where(LLMRequest.created_at >= since)
        .group_by(text("1"))
        .order_by(text("1"))
    )

    llm_hourly = [
        {"hour": r.hour, "count": r.count, "tokens": r.tokens or 0}
        for r in llm_rows
    ]

    # Agent status breakdown
    status_rows = await session.execute(
        select(Agent.status, func.count().label("n")).group_by(Agent.status)
    )
    status_dist = {r.status: r.n for r in status_rows}

    # Average latency by agent (last 24h)
    perf_rows = await session.execute(
        select(
            Agent.name,
            func.avg(PerformanceMetric.value).label("avg_ms"),
        )
        .join(PerformanceMetric, Agent.agent_id == PerformanceMetric.agent_id)
        .where(
            PerformanceMetric.metric_name == "action_latency_ms",
            PerformanceMetric.recorded_at >= since,
        )
        .group_by(Agent.name)
    )
    latency_by_agent = [
        {"agent": r.name, "avg_latency_ms": round(r.avg_ms, 1)} for r in perf_rows
    ]

    return {
        "llm_hourly": llm_hourly,
        "status_distribution": status_dist,
        "latency_by_agent": latency_by_agent,
    }
