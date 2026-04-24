"""Chibu gRPC client — streaming execution and info retrieval."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import grpc
from grpc import aio

logger = logging.getLogger("chibu.grpc.client")


@dataclass
class ExecuteEvent:
    event_type: str
    content: str
    tool_name: str = ""
    is_done: bool = False
    timestamp: int = 0


class ChibuClient:
    """Async gRPC client for a single Chibu Pi agent."""

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

        options = [
            ("grpc.keepalive_time_ms", 20_000),
            ("grpc.keepalive_timeout_ms", 5_000),
            ("grpc.max_send_message_length", 32 * 1024 * 1024),
            ("grpc.max_receive_message_length", 32 * 1024 * 1024),
        ]
        self._channel = aio.insecure_channel(self.address, options=options)
        self._stub = chibu_agent_pb2_grpc.ChiAgentStub(self._channel)

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
            self._channel = None
            self._stub = None

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

    async def get_info(self) -> dict:
        from chibu.grpc_server import chibu_agent_pb2

        resp = await self._stub.GetInfo(
            chibu_agent_pb2.InfoRequest(auth_token=self.auth_token),
            timeout=10.0,
        )
        return {
            "agent_id": resp.agent_id,
            "name": resp.name,
            "agent_group": resp.agent_group,
            "version": resp.version,
            "skills": list(resp.skills),
            "models": list(resp.models),
            "status": resp.status,
        }

    async def execute(
        self,
        prompt: str,
        session_id: str = "",
        model_id: str = "",
        context: dict | None = None,
    ) -> AsyncGenerator[ExecuteEvent, None]:
        from chibu.grpc_server import chibu_agent_pb2

        request = chibu_agent_pb2.ExecuteRequest(
            auth_token=self.auth_token,
            prompt=prompt,
            session_id=session_id,
            model_id=model_id,
            context=context or {},
        )

        try:
            async for chunk in self._stub.Execute(request, timeout=self.timeout):
                yield ExecuteEvent(
                    event_type=chunk.event_type,
                    content=chunk.content,
                    tool_name=chunk.tool_name,
                    is_done=chunk.is_done,
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
        """Convenience: collect full streaming response into a string."""
        parts: list[str] = []
        async for event in self.execute(prompt, **kwargs):
            if event.event_type == "text":
                parts.append(event.content)
        return "".join(parts)
