"""Agent CRUD, lifecycle, skills, and extensions endpoints."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel

from chibu.control_plane.deps import get_process_manager, get_registry
from chibu.db.models import Agent
from chibu.process.manager import AgentProcessManager
from chibu.registry.agent_registry import AgentRegistry
from chibu.utils.filesystem import bootstrap_agent_root

router = APIRouter(tags=["agents"])
logger = logging.getLogger("chibu.control_plane.agents")


# ── Request models ────────────────────────────────────────────────────────────

class CreateAgentRequest(BaseModel):
    name: str
    chiboo: str           # chiboo (group) name
    grpc_port: int | None = None


# ── Agent CRUD ────────────────────────────────────────────────────────────────

@router.get("/")
async def list_agents(
    chiboo: str | None = None,
    registry: AgentRegistry = Depends(get_registry),
) -> list[dict]:
    if chiboo:
        group = await registry.get_group_by_name(chiboo)
        group_id = group.id if group else None
    else:
        group_id = None
    agents = await registry.list_agents(group_id=group_id)
    return [_agent_dict(a) for a in agents]


@router.post("/")
async def create_agent(
    body: CreateAgentRequest,
    registry: AgentRegistry = Depends(get_registry),
    pm: AgentProcessManager = Depends(get_process_manager),
) -> dict:
    agents_dir = Path(os.getenv("CHIBU_AGENTS_DIR", "agents"))
    workspace = agents_dir / body.chiboo / body.name

    agent = await registry.create_agent(
        name=body.name,
        group_name=body.chiboo,
        workspace_path=str(workspace.resolve()),
        grpc_port=body.grpc_port,
    )

    bootstrap_agent_root(workspace, agent.agent_id, agent.name, body.chiboo)
    _flush_snapshot(pm, registry)

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

    await registry.delete_agent(agent_id)
    _flush_snapshot(pm, registry)
    return {"ok": True}


# ── Lifecycle ─────────────────────────────────────────────────────────────────

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
    _flush_snapshot(pm, registry)

    pid = await pm.start(_agent_record(agent))
    # pm.start() awaits _wait_ready(); only set running if process is still alive
    if pm.is_running(agent_id):
        await registry.update_status(agent_id, "running", pid=pid)
        return {"status": "running", "pid": pid}
    else:
        await registry.update_status(agent_id, "error", pid=pid)
        return {"status": "error", "pid": pid}


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

    if agent.status == "running" and not pm.is_running(agent_id):
        await registry.update_status(agent_id, "stopped", pid=None)
        return {"status": "stopped", "pid": None}

    return {"status": agent.status, "pid": agent.pid}


# ── Skills ────────────────────────────────────────────────────────────────────

@router.get("/{agent_id}/skills")
async def list_skills(
    agent_id: str,
    registry: AgentRegistry = Depends(get_registry),
) -> list[dict]:
    agent = await registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return _list_skills(Path(agent.workspace_path))


@router.post("/{agent_id}/skills")
async def add_skill(
    agent_id: str,
    skill_name: str = Body(..., embed=True),
    skill_content: str = Body(..., embed=True),
    registry: AgentRegistry = Depends(get_registry),
    pm: AgentProcessManager = Depends(get_process_manager),
) -> dict:
    """Create a new skill directory with a SKILL.md file, then hot-reload."""
    agent = await registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    skill_dir = Path(agent.workspace_path) / ".pi" / "skills" / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_content)

    await _hot_reload(agent, pm, registry)
    return {"ok": True, "skill": skill_name, "reloaded": agent.status == "running"}


@router.delete("/{agent_id}/skills/{skill_name}")
async def remove_skill(
    agent_id: str,
    skill_name: str,
    registry: AgentRegistry = Depends(get_registry),
    pm: AgentProcessManager = Depends(get_process_manager),
) -> dict:
    agent = await registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    skill_dir = Path(agent.workspace_path) / ".pi" / "skills" / skill_name
    if not skill_dir.exists():
        raise HTTPException(404, f"Skill '{skill_name}' not found")

    shutil.rmtree(skill_dir)
    await _hot_reload(agent, pm, registry)
    return {"ok": True, "reloaded": agent.status == "running"}


# ── Extensions ────────────────────────────────────────────────────────────────

@router.get("/{agent_id}/extensions")
async def list_extensions(
    agent_id: str,
    registry: AgentRegistry = Depends(get_registry),
) -> list[dict]:
    agent = await registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return _list_extensions(Path(agent.workspace_path))


@router.post("/{agent_id}/extensions")
async def add_extension(
    agent_id: str,
    ext_name: str = Body(..., embed=True),
    ext_content: str = Body(..., embed=True),
    registry: AgentRegistry = Depends(get_registry),
    pm: AgentProcessManager = Depends(get_process_manager),
) -> dict:
    """Write a TypeScript extension file to .pi/extensions/ then hot-reload."""
    agent = await registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    ext_dir = Path(agent.workspace_path) / ".pi" / "extensions"
    ext_dir.mkdir(parents=True, exist_ok=True)
    fname = ext_name if ext_name.endswith(".ts") else f"{ext_name}.ts"
    (ext_dir / fname).write_text(ext_content)

    await _hot_reload(agent, pm, registry)
    return {"ok": True, "extension": fname, "reloaded": agent.status == "running"}


@router.delete("/{agent_id}/extensions/{ext_name}")
async def remove_extension(
    agent_id: str,
    ext_name: str,
    registry: AgentRegistry = Depends(get_registry),
    pm: AgentProcessManager = Depends(get_process_manager),
) -> dict:
    agent = await registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    ext_dir = Path(agent.workspace_path) / ".pi" / "extensions"
    fname = ext_name if ext_name.endswith(".ts") else f"{ext_name}.ts"
    target = ext_dir / fname
    if not target.exists():
        raise HTTPException(404, f"Extension '{fname}' not found")

    target.unlink()
    await _hot_reload(agent, pm, registry)
    return {"ok": True, "reloaded": agent.status == "running"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _agent_dict(agent: Agent) -> dict:
    chiboo = agent.group.name if agent.group else ""
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "chiboo": chiboo,
        "grpc_port": agent.grpc_port,
        "status": agent.status,
        "pid": agent.pid,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
        "auth_token": agent.auth_token,
    }


def _agent_record(agent: Agent) -> dict:
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "chiboo": agent.group.name if agent.group else "",
        "auth_token": agent.auth_token,
        "grpc_port": agent.grpc_port,
        "workspace_path": agent.workspace_path,
    }


def _list_skills(workspace: Path) -> list[dict]:
    skills_dir = workspace / ".pi" / "skills"
    if not skills_dir.exists():
        return []
    result = []
    for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        skill_md = d / "SKILL.md"
        result.append({
            "name": d.name,
            "has_skill_md": skill_md.exists(),
        })
    return result


def _list_extensions(workspace: Path) -> list[dict]:
    ext_dir = workspace / ".pi" / "extensions"
    if not ext_dir.exists():
        return []
    return [
        {"name": f.name}
        for f in sorted(ext_dir.glob("*.ts"))
    ]


async def _hot_reload(
    agent: Agent,
    pm: AgentProcessManager,
    registry: AgentRegistry,
) -> None:
    """If the agent is running, reload its pi subprocess via gRPC Reload RPC."""
    if agent.status != "running":
        return

    try:
        from chibu.grpc_server.client import ChibuClient
        async with ChibuClient("127.0.0.1", agent.grpc_port, agent.auth_token) as client:
            ok = await client.reload()
            if not ok:
                logger.warning("Reload returned ok=false for agent %s", agent.agent_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Hot-reload failed for agent %s: %s", agent.agent_id, exc)


def _flush_snapshot(pm: AgentProcessManager, registry: AgentRegistry) -> None:
    import asyncio

    async def _do():
        try:
            agents = await registry.list_agents()
            pm.write_registry_snapshot([_agent_record(a) for a in agents])
        except Exception:  # noqa: BLE001
            pass

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_do())
    except Exception:  # noqa: BLE001
        pass
