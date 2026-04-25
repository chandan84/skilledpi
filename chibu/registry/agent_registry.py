"""Agent registry backed by SQLAlchemy — SQLite default, PostgreSQL portable."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from chibu.db.models import Agent, AgentEvent, AgentGroup
from chibu.utils.auth import generate_token

_PORT_START = 50051
_PORT_END = 50200


def _slug(value: str) -> str:
    """Lowercase, strip non-alphanumeric except hyphens."""
    return re.sub(r"[^a-z0-9\-]", "", value.lower().replace("_", "-").replace(" ", "-"))


class AgentRegistry:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── Chiboos (groups) ──────────────────────────────────────────────────────

    async def list_groups(self) -> Sequence[AgentGroup]:
        result = await self._s.execute(
            select(AgentGroup)
            .options(selectinload(AgentGroup.agents))
            .order_by(AgentGroup.name)
        )
        return result.scalars().all()

    async def get_group_by_name(self, name: str) -> AgentGroup | None:
        result = await self._s.execute(
            select(AgentGroup).where(AgentGroup.name == name)
        )
        return result.scalar_one_or_none()

    async def get_or_create_group(self, name: str, description: str = "") -> AgentGroup:
        group = await self.get_group_by_name(name)
        if group is None:
            import uuid
            group = AgentGroup(id=str(uuid.uuid4()), name=name, description=description)
            self._s.add(group)
            await self._s.flush()
        return group

    async def delete_group(self, group_id: str) -> bool:
        result = await self._s.execute(
            select(AgentGroup).where(AgentGroup.id == group_id)
        )
        group = result.scalar_one_or_none()
        if group is None:
            return False
        await self._s.delete(group)
        return True

    # ── Agents ────────────────────────────────────────────────────────────────

    async def list_agents(self, group_id: str | None = None) -> Sequence[Agent]:
        stmt = (
            select(Agent)
            .options(selectinload(Agent.group))
            .order_by(Agent.created_at.desc())
        )
        if group_id:
            stmt = stmt.where(Agent.group_id == group_id)
        result = await self._s.execute(stmt)
        return result.scalars().all()

    async def get_agent(self, agent_id: str) -> Agent | None:
        result = await self._s.execute(
            select(Agent)
            .options(selectinload(Agent.group))
            .where(Agent.agent_id == agent_id)
        )
        return result.scalar_one_or_none()

    async def create_agent(
        self,
        name: str,
        group_name: str,
        workspace_path: str,
        grpc_port: int | None = None,
        description: str = "",
    ) -> Agent:
        group = await self.get_or_create_group(group_name, description)

        # Composite human-readable ID
        agent_id = f"{_slug(group_name)}_{_slug(name)}"

        # Retry port allocation on collision (concurrent creates can race)
        for _attempt in range(10):
            port = grpc_port or await self._next_port()
            agent = Agent(
                agent_id=agent_id,
                name=name,
                group_id=group.id,
                auth_token=generate_token(),
                grpc_port=port,
                workspace_path=workspace_path,
                status="stopped",
            )
            self._s.add(agent)
            try:
                await self._s.flush()
                break
            except IntegrityError:
                await self._s.rollback()
                grpc_port = None  # let _next_port() try again
        else:
            raise RuntimeError("Could not allocate a unique gRPC port after 10 attempts")

        await self._record_event(agent_id, "agent_created", {"name": name, "chiboo": group_name})
        return await self.get_agent(agent_id)

    async def update_status(
        self,
        agent_id: str,
        status: str,
        pid: int | None = -1,
    ) -> Agent | None:
        agent = await self.get_agent(agent_id)
        if agent is None:
            return None
        agent.status = status
        if pid != -1:
            agent.pid = pid
        agent.updated_at = datetime.now(timezone.utc)
        await self._s.flush()
        await self._record_event(agent_id, "status_changed", {"status": status})
        return agent

    async def delete_agent(self, agent_id: str) -> bool:
        agent = await self.get_agent(agent_id)
        if agent is None:
            return False
        await self._record_event(agent_id, "agent_deleted", {})
        await self._s.delete(agent)
        return True

    # ── Dashboard ─────────────────────────────────────────────────────────────

    async def dashboard_summary(self) -> dict:
        total = await self._s.scalar(select(func.count()).select_from(Agent))
        running = await self._s.scalar(
            select(func.count()).select_from(Agent).where(Agent.status == "running")
        )
        total_groups = await self._s.scalar(
            select(func.count()).select_from(AgentGroup)
        )
        return {
            "total_agents": total or 0,
            "running_agents": running or 0,
            "stopped_agents": (total or 0) - (running or 0),
            "total_chiboos": total_groups or 0,
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _next_port(self) -> int:
        """Return the lowest port in [_PORT_START, _PORT_END] not currently assigned."""
        result = await self._s.execute(select(Agent.grpc_port))
        used = set(result.scalars().all())
        for port in range(_PORT_START, _PORT_END + 1):
            if port not in used:
                return port
        raise RuntimeError("gRPC port range exhausted")

    async def _record_event(self, agent_id: str, event_type: str, payload: dict) -> None:
        self._s.add(AgentEvent(agent_id=agent_id, event_type=event_type, payload=payload))
        await self._s.flush()
        try:
            from chibu.honker import agent_events_stream
            agent_events_stream().publish({
                "agent_id": agent_id,
                "event_type": event_type,
                "payload": payload,
            })
        except Exception:  # noqa: BLE001
            pass  # Honker not initialised (test/CLI contexts)
