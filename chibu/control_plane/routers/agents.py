"""Agent CRUD and lifecycle endpoints."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from chibu.control_plane.app import templates
from chibu.control_plane.deps import get_process_manager, get_registry
from chibu.db.engine import get_session
from chibu.db.models import Agent, AgentEvent
from chibu.process.manager import AgentProcessManager
from chibu.registry.agent_registry import AgentRegistry
from chibu.utils.filesystem import bootstrap_agent_root

router = APIRouter(tags=["agents"])
logger = logging.getLogger("chibu.control_plane.agents")


class CreateAgentRequest(BaseModel):
    name: str
    agent_group: str
    grpc_port: int | None = None


# ── HTML pages ────────────────────────────────────────────────────────────────


@router.get("/{agent_id}", response_class=HTMLResponse)
async def agent_detail_page(
    request: Request,
    agent_id: str,
    registry: AgentRegistry = Depends(get_registry),
):
    agent = await registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    skills = _list_skills(Path(agent.root_path))
    return templates.TemplateResponse(
        "agent_detail.html",
        {
            "request": request,
            "agent": agent,
            "skills": skills,
            "auth_token_masked": agent.auth_token[:8] + "•" * 32,
        },
    )


# ── JSON API ──────────────────────────────────────────────────────────────────


@router.get("/")
async def list_agents(
    group_id: str | None = None,
    registry: AgentRegistry = Depends(get_registry),
) -> list[dict]:
    agents = await registry.list_agents(group_id=group_id)
    return [_agent_dict(a) for a in agents]


@router.post("/")
async def create_agent(
    body: CreateAgentRequest,
    registry: AgentRegistry = Depends(get_registry),
    pm: AgentProcessManager = Depends(get_process_manager),
) -> dict:
    agents_dir = Path(os.getenv("CHIBU_AGENTS_DIR", "agents"))
    root = agents_dir / body.name

    agent = await registry.create_agent(
        name=body.name,
        group_name=body.agent_group,
        root_path=str(root.resolve()),
        grpc_port=body.grpc_port,
    )

    bootstrap_agent_root(root, agent.agent_id, agent.name)
    _flush_registry_snapshot(pm, registry)

    return _agent_dict(agent)


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    registry: AgentRegistry = Depends(get_registry),
    pm: AgentProcessManager = Depends(get_process_manager),
) -> dict:
    agent = await registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    if agent.status == "running":
        raise HTTPException(409, "Stop the agent before deleting it")

    deleted = await registry.delete_agent(agent_id)
    return {"ok": deleted}


@router.post("/{agent_id}/start")
async def start_agent(
    agent_id: str,
    registry: AgentRegistry = Depends(get_registry),
    pm: AgentProcessManager = Depends(get_process_manager),
) -> dict:
    agent = await registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    if agent.status in ("running", "starting"):
        return {"status": agent.status, "pid": agent.pid}

    await registry.update_status(agent_id, "starting")
    _flush_registry_snapshot(pm, registry)

    agent_rec = _agent_record_for_subprocess(agent)
    pid = await pm.start(agent_rec)
    await registry.update_status(agent_id, "running", pid=pid)
    return {"status": "running", "pid": pid}


@router.post("/{agent_id}/stop")
async def stop_agent(
    agent_id: str,
    registry: AgentRegistry = Depends(get_registry),
    pm: AgentProcessManager = Depends(get_process_manager),
) -> dict:
    agent = await registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    if agent.status not in ("running", "starting"):
        return {"status": agent.status}

    await pm.stop(agent_id, pid=agent.pid)
    await registry.update_status(agent_id, "stopped", pid=None)
    return {"status": "stopped"}


@router.get("/{agent_id}/status")
async def agent_status(
    agent_id: str,
    registry: AgentRegistry = Depends(get_registry),
    pm: AgentProcessManager = Depends(get_process_manager),
) -> dict:
    agent = await registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    actual_running = pm.is_running(agent_id)
    if agent.status == "running" and not actual_running:
        await registry.update_status(agent_id, "stopped", pid=None)
        return {"status": "stopped", "pid": None}

    return {"status": agent.status, "pid": agent.pid}


@router.get("/{agent_id}/skills")
async def list_skills(
    agent_id: str,
    registry: AgentRegistry = Depends(get_registry),
) -> list[dict]:
    agent = await registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return _list_skills(Path(agent.root_path))


# ── helpers ───────────────────────────────────────────────────────────────────


def _agent_dict(agent: Agent) -> dict:
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "group_id": agent.group_id,
        "group_name": agent.group.name if agent.group else "",
        "grpc_port": agent.grpc_port,
        "status": agent.status,
        "pid": agent.pid,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
        "auth_token": agent.auth_token,
    }


def _agent_record_for_subprocess(agent: Agent) -> dict:
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "agent_group": agent.group.name if agent.group else "",
        "auth_token": agent.auth_token,
        "grpc_port": agent.grpc_port,
        "root_path": agent.root_path,
    }


def _list_skills(root: Path) -> list[dict]:
    skills_dir = root / ".pi" / "skills"
    if not skills_dir.exists():
        return []
    result = []
    for f in sorted(skills_dir.glob("*.py")):
        result.append({"name": f.stem, "file": f.name})
    return result


def _flush_registry_snapshot(
    pm: AgentProcessManager,
    registry: AgentRegistry,
) -> None:
    """Write registry snapshot after each mutation — done in background; errors are non-fatal."""
    import asyncio

    async def _flush():
        try:
            agents = await registry.list_agents()
            records = [_agent_record_for_subprocess(a) for a in agents]
            pm.write_registry_snapshot(records)
        except Exception:  # noqa: BLE001
            pass  # session may be closed after request commit; snapshot is best-effort

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_flush())
    except Exception:  # noqa: BLE001
        pass
