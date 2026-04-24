"""WebSocket and SSE endpoints for real-time agent log streaming."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from chibu.control_plane.deps import get_registry
from chibu.registry.agent_registry import AgentRegistry

router = APIRouter(tags=["realtime"])
logger = logging.getLogger("chibu.control_plane.ws")

_TAIL_LINES = 200
_POLL_INTERVAL = 0.5


@router.websocket("/{agent_id}/logs")
async def ws_agent_logs(
    websocket: WebSocket,
    agent_id: str,
    registry: AgentRegistry = Depends(get_registry),
) -> None:
    """Stream agent.log over WebSocket, tailing new lines as they appear."""
    agent = await registry.get_agent(agent_id)
    if not agent:
        await websocket.close(code=4004, reason="Agent not found")
        return

    log_path = Path(agent.root_path) / "agent.log"
    await websocket.accept()

    # Send last N lines first as history
    if log_path.exists():
        history = _tail(log_path, _TAIL_LINES)
        for line in history:
            await websocket.send_text(line)

    # Then stream new lines
    try:
        offset = log_path.stat().st_size if log_path.exists() else 0
        while True:
            await asyncio.sleep(_POLL_INTERVAL)
            if not log_path.exists():
                continue
            current_size = log_path.stat().st_size
            if current_size <= offset:
                continue
            async with aiofiles.open(log_path, "r") as f:
                await f.seek(offset)
                new_content = await f.read()
            offset = current_size
            for line in new_content.splitlines():
                if line:
                    await websocket.send_text(line)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("Log stream closed: %s", exc)


@router.get("/{agent_id}/logs/sse")
async def sse_agent_logs(
    agent_id: str,
    registry: AgentRegistry = Depends(get_registry),
) -> StreamingResponse:
    """SSE endpoint for agent log tailing (alternative to WebSocket)."""
    agent = await registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    log_path = Path(agent.root_path) / "agent.log"

    async def event_stream():
        history = _tail(log_path, _TAIL_LINES) if log_path.exists() else []
        for line in history:
            yield f"data: {line}\n\n"

        offset = log_path.stat().st_size if log_path.exists() else 0
        while True:
            await asyncio.sleep(_POLL_INTERVAL)
            if not log_path.exists():
                yield ": keepalive\n\n"
                continue
            current_size = log_path.stat().st_size
            if current_size <= offset:
                yield ": keepalive\n\n"
                continue
            async with aiofiles.open(log_path, "r") as f:
                await f.seek(offset)
                content = await f.read()
            offset = current_size
            for line in content.splitlines():
                if line:
                    yield f"data: {line}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _tail(path: Path, n: int) -> list[str]:
    try:
        text = path.read_text(errors="replace")
        return text.splitlines()[-n:]
    except Exception:
        return []
