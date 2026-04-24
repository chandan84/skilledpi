"""Chibu gRPC client — used by the control plane to talk to agent processes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import grpc
from grpc import aio

logger = logging.getLogger("chibu.grpc.client")

_CHANNEL_OPTIONS = [
    ("grpc.keepalive_time_ms", 20_000),
    ("grpc.keepalive_timeout_ms", 5_000),
    ("grpc.max_send_message_length", 32 * 1024 * 1024),
    ("grpc.max_receive_message_length", 32 * 1024 * 1024),
]


@dataclass
class ExecuteEvent:
    event_type: str
    content: str
    tool_name: str = ""
    is_done: bool = False
    session_id: str = ""
    timestamp: int = 0


class ChibuClient:
    """Async gRPC client for a single Chibu pi agent."""

    def __init__(
        self,
        host: str,
        port: int,
        auth_token: str,
        timeout: float = 120.0,
    ) -> None:
        self.address = f"{host}:{port}"
        self.auth_token = auth_token
        self.timeout = timeout
        self._channel: aio.Channel | None = None
        self._stub = None

    async def __aenter__(self) -> "ChibuClient":
        await self.connect()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    async def connect(self) -> None:
        from chibu.grpc_server import chibu_agent_pb2_grpc
        self._channel = aio.insecure_channel(self.address, options=_CHANNEL_OPTIONS)
        self._stub = chibu_agent_pb2_grpc.ChiAgentStub(self._channel)

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
            self._channel = None
            self._stub = None

    # ── health ────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        from chibu.grpc_server import chibu_agent_pb2
        try:
            resp = await asyncio.wait_for(
                self._stub.Ping(chibu_agent_pb2.PingRequest(message="health")),
                timeout=5.0,
            )
            return resp.message.startswith("pong")
        except Exception:
            return False

    # ── info ──────────────────────────────────────────────────────────────────

    async def get_info(self) -> dict:
        from chibu.grpc_server import chibu_agent_pb2
        resp = await self._stub.GetInfo(
            chibu_agent_pb2.InfoRequest(auth_token=self.auth_token),
            timeout=10.0,
        )
        return {
            "agent_id": resp.agent_id,
            "name": resp.name,
            "chiboo": resp.chiboo,
            "skills": list(resp.skills),
            "status": resp.status,
            "grpc_port": resp.grpc_port,
            "pid": resp.pid,
        }

    # ── execute ───────────────────────────────────────────────────────────────

    async def execute(
        self,
        prompt: str,
        model: str = "faah",
        new_session: bool = False,
        compact_first: bool = False,
        files: list[str] | None = None,
        timeout_seconds: int = 120,
        session_id: str = "",
    ) -> AsyncGenerator[ExecuteEvent, None]:
        from chibu.grpc_server import chibu_agent_pb2

        request = chibu_agent_pb2.ExecuteRequest(
            auth_token=self.auth_token,
            prompt=prompt,
            session_id=session_id,
            model=model,
            new_session=new_session,
            compact_first=compact_first,
            files=files or [],
            timeout_seconds=timeout_seconds,
        )

        try:
            async for chunk in self._stub.Execute(request, timeout=self.timeout):
                yield ExecuteEvent(
                    event_type=chunk.event_type,
                    content=chunk.content,
                    tool_name=chunk.tool_name,
                    is_done=chunk.is_done,
                    session_id=chunk.session_id,
                    timestamp=chunk.timestamp,
                )
        except grpc.RpcError as exc:
            logger.error("gRPC stream error: %s %s", exc.code(), exc.details())
            yield ExecuteEvent(
                event_type="error",
                content=f"{exc.code()}: {exc.details()}",
                is_done=True,
            )

    async def execute_sync(self, prompt: str, **kwargs) -> str:
        parts: list[str] = []
        async for event in self.execute(prompt, **kwargs):
            if event.event_type == "text":
                parts.append(event.content)
        return "".join(parts)

    # ── reload ────────────────────────────────────────────────────────────────

    async def reload(self) -> bool:
        from chibu.grpc_server import chibu_agent_pb2
        try:
            resp = await asyncio.wait_for(
                self._stub.Reload(
                    chibu_agent_pb2.ReloadRequest(auth_token=self.auth_token)
                ),
                timeout=30.0,
            )
            return resp.ok
        except Exception as exc:
            logger.error("Reload failed: %s", exc)
            return False
