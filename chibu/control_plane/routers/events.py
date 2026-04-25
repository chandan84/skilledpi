"""Durable AgentEvent stream — SSE endpoint for real-time lifecycle events."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

router = APIRouter(tags=["events"])
logger = logging.getLogger("chibu.control_plane.events")


@router.get("/stream")
async def events_stream(
    consumer: str = Query(default="sse", description="Consumer ID for offset tracking"),
    agent_id: str | None = Query(default=None, description="Filter to a single agent"),
) -> StreamingResponse:
    """Server-Sent Events stream of AgentEvent records in insertion order.

    Reconnect-safe: pass the same `consumer` ID and events resume from where
    the consumer last left off.
    """

    async def generate():
        try:
            from chibu.honker import agent_events_stream
            stream = agent_events_stream()
            async for ev in stream.subscribe(consumer=consumer):
                if agent_id and ev.payload.get("agent_id") != agent_id:
                    continue
                data = json.dumps({
                    "offset": ev.offset,
                    "agent_id": ev.payload.get("agent_id"),
                    "event_type": ev.payload.get("event_type"),
                    "payload": ev.payload.get("payload", {}),
                })
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("Event stream closed: %s", exc)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
