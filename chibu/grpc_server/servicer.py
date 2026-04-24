"""ChiAgent gRPC servicer — bridges gRPC requests to PiAgent.execute()."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

logger = logging.getLogger("chibu.grpc.servicer")


def build_servicer(agent):
    """Return a ChiAgentServicer bound to *agent* (lazy import of pb2)."""

    from chibu.grpc_server import chibu_agent_pb2, chibu_agent_pb2_grpc

    class ChiAgentServicer(chibu_agent_pb2_grpc.ChiAgentServicer):
        # ── Execute ──────────────────────────────────────────────────────────

        async def Execute(self, request, context):
            if not agent.verify_token(request.auth_token):
                await context.abort(
                    grpc_code=16,  # UNAUTHENTICATED
                    details="Invalid auth token",
                )
                return

            session_id = request.session_id or str(uuid.uuid4())
            extra_ctx = dict(request.context) if request.context else {}

            try:
                async for event in agent.execute(
                    prompt=request.prompt,
                    session_id=session_id,
                    model_id=request.model_id or "",
                    context=extra_ctx,
                ):
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
                agent_group=info["agent_group"],
                version=info["version"],
                skills=info["skills"],
                models=info["models"],
                status=info["status"],
            )

        # ── ListSkills ───────────────────────────────────────────────────────

        async def ListSkills(self, request, context):
            if not agent.verify_token(request.auth_token):
                await context.abort(16, "Invalid auth token")
                return

            skill_infos = [
                chibu_agent_pb2.SkillInfo(
                    name=s.name,
                    description=s.description,
                    version=s.version,
                    parameters=list(
                        s.input_schema.get("properties", {}).keys()
                    ),
                )
                for s in agent._skills.values()
            ]
            return chibu_agent_pb2.ListSkillsResponse(skills=skill_infos)

        # ── Ping ─────────────────────────────────────────────────────────────

        async def Ping(self, request, context):
            return chibu_agent_pb2.PongResponse(
                message=f"pong:{request.message}",
                timestamp=int(time.time() * 1000),
            )

    return ChiAgentServicer()
