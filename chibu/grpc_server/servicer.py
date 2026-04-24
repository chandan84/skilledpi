"""ChiAgent gRPC servicer — bridges gRPC requests to PiAgent.

All LLM work is done by the pi subprocess managed by PiAgent.
This file contains zero AI/LLM logic; it only:
  - validates auth tokens
  - translates gRPC request fields to PiAgent.execute() kwargs
  - maps AgentEvent objects to proto ExecuteResponse messages
  - streams them back to the caller
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("chibu.grpc.servicer")


def build_servicer(agent):
    """Return a ChiAgentServicer bound to *agent* (lazy-imports pb2)."""

    from chibu.grpc_server import chibu_agent_pb2, chibu_agent_pb2_grpc
    from chibu.otel.tracing import record_execute_span

    class ChiAgentServicer(chibu_agent_pb2_grpc.ChiAgentServicer):

        # ── Execute ──────────────────────────────────────────────────────────

        async def Execute(self, request, context):
            if not agent.verify_token(request.auth_token):
                await context.abort(16, "Invalid auth token")
                return

            model = request.model or "faah"
            session_id = request.session_id or agent._current_session_id
            files = list(request.files) if request.files else []
            timeout = request.timeout_seconds if request.timeout_seconds > 0 else 120

            with record_execute_span(agent.agent_id, agent.chiboo, model):
                try:
                    async for event in agent.execute(
                        prompt=request.prompt,
                        model=model,
                        new_session=request.new_session,
                        compact_first=request.compact_first,
                        files=files,
                        timeout_seconds=timeout,
                    ):
                        # Refresh session_id once pi reports it
                        if agent._current_session_id:
                            session_id = agent._current_session_id

                        yield chibu_agent_pb2.ExecuteResponse(
                            content=event.content,
                            is_done=event.is_done,
                            event_type=event.event_type,
                            session_id=session_id,
                            timestamp=int(time.time() * 1000),
                            tool_name=event.tool_name,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Execute error: %s", exc)
                    yield chibu_agent_pb2.ExecuteResponse(
                        content=str(exc),
                        is_done=True,
                        event_type="error",
                        session_id=session_id,
                        timestamp=int(time.time() * 1000),
                    )

        # ── GetInfo ──────────────────────────────────────────────────────────

        async def GetInfo(self, request, context):
            if not agent.verify_token(request.auth_token):
                await context.abort(16, "Invalid auth token")
                return

            info = agent.info()
            return chibu_agent_pb2.InfoResponse(
                agent_id=info["agent_id"],
                name=info["name"],
                chiboo=info["chiboo"],
                skills=info["skills"],
                status=info["status"],
                grpc_port=agent.grpc_port,
                pid=str(info["pid"] or ""),
            )

        # ── ListSkills ───────────────────────────────────────────────────────

        async def ListSkills(self, request, context):
            if not agent.verify_token(request.auth_token):
                await context.abort(16, "Invalid auth token")
                return

            skills = agent.list_skills()
            return chibu_agent_pb2.ListSkillsResponse(
                skills=[
                    chibu_agent_pb2.SkillInfo(
                        name=s["name"],
                        description=s.get("description", ""),
                        license=s.get("license", ""),
                    )
                    for s in skills
                ]
            )

        # ── Reload ───────────────────────────────────────────────────────────

        async def Reload(self, request, context):
            if not agent.verify_token(request.auth_token):
                await context.abort(16, "Invalid auth token")
                return

            try:
                await agent.restart()
                return chibu_agent_pb2.ReloadResponse(ok=True, message="Agent reloaded")
            except Exception as exc:  # noqa: BLE001
                logger.exception("Reload failed: %s", exc)
                return chibu_agent_pb2.ReloadResponse(ok=False, message=str(exc))

        # ── Ping ─────────────────────────────────────────────────────────────

        async def Ping(self, request, context):
            return chibu_agent_pb2.PongResponse(
                message=f"pong:{request.message}",
                timestamp=int(time.time() * 1000),
            )

    return ChiAgentServicer()
