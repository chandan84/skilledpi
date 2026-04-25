"""WebSocket log tailing and SSE execute endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from chibu.control_plane.deps import get_registry
from chibu.registry.agent_registry import AgentRegistry

router = APIRouter(tags=["realtime"])
logger = logging.getLogger("chibu.control_plane.ws")

_TAIL_LINES = 200
_POLL_INTERVAL = 0.4


# ── SSE execute (used by the dashboard UI) ────────────────────────────────────

class ExecuteBody(BaseModel):
    prompt: str
    model: str = "faah"
    new_session: bool = False
    compact_first: bool = False
    files: list[str] = []
    timeout_seconds: int = 120


@router.post("/{agent_id}/execute")
async def sse_execute(
    agent_id: str,
    body: ExecuteBody,
    registry: AgentRegistry = Depends(get_registry),
) -> StreamingResponse:
    """Stream Execute events as Server-Sent Events for the dashboard UI."""
    agent = await registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    if agent.status != "running":
        raise HTTPException(409, "Agent is not running")

    async def event_stream():
        try:
            from chibu.grpc_server.client import ChibuClient
            async with ChibuClient(
                "127.0.0.1", agent.grpc_port, agent.auth_token,
                timeout=body.timeout_seconds + 5,
            ) as client:
                async for ev in client.execute(
                    prompt=body.prompt,
                    model=body.model,
                    new_session=body.new_session,
                    compact_first=body.compact_first,
                    files=body.files,
                    timeout_seconds=body.timeout_seconds,
                ):
                    payload = json.dumps({
                        "event_type": ev.event_type,
                        "content": ev.content,
                        "tool_name": ev.tool_name,
                        "is_done": ev.is_done,
                        "session_id": ev.session_id,
                    })
                    yield f"data: {payload}\n\n"
                    if ev.is_done:
                        break
        except Exception as exc:  # noqa: BLE001
            payload = json.dumps({"event_type": "error", "content": str(exc), "is_done": True})
            yield f"data: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── WebSocket log tail ────────────────────────────────────────────────────────

@router.websocket("/{agent_id}/logs")
async def ws_logs(
    websocket: WebSocket,
    agent_id: str,
    registry: AgentRegistry = Depends(get_registry),
) -> None:
    agent = await registry.get_agent(agent_id)
    if not agent:
        await websocket.close(code=4004, reason="Agent not found")
        return

    log_path = Path(agent.workspace_path) / "agent.log"
    await websocket.accept()

    if log_path.exists():
        for line in _tail(log_path, _TAIL_LINES):
            await websocket.send_text(line)

    try:
        offset = log_path.stat().st_size if log_path.exists() else 0
        while True:
            await asyncio.sleep(_POLL_INTERVAL)
            if not log_path.exists():
                continue
            size = log_path.stat().st_size
            if size <= offset:
                continue
            async with aiofiles.open(log_path, "r") as f:
                await f.seek(offset)
                new_text = await f.read()
            offset = size
            for line in new_text.splitlines():
                if line:
                    await websocket.send_text(line)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("Log stream closed: %s", exc)


def _tail(path: Path, n: int) -> list[str]:
    try:
        return path.read_text(errors="replace").splitlines()[-n:]
    except Exception:
        return []
