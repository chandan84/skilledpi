"""Agent registry backed by SQLAlchemy — SQLite or PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from chibu.db.models import Agent, AgentEvent, AgentGroup
from chibu.utils.auth import generate_token

_PORT_START = 50051
_PORT_END = 50200


class AgentRegistry:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── Groups ───────────────────────────────────────────────────────────────

    async def list_groups(self) -> Sequence[AgentGroup]:
        result = await self._s.execute(
            select(AgentGroup)
            .options(selectinload(AgentGroup.agents))
            .order_by(AgentGroup.name)
        )
        return result.scalars().all()

    async def get_or_create_group(self, name: str, description: str = "") -> AgentGroup:
        result = await self._s.execute(
            select(AgentGroup).where(AgentGroup.name == name)
        )
        group = result.scalar_one_or_none()
        if group is None:
            group = AgentGroup(
                id=str(uuid.uuid4()), name=name, description=description
            )
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

    # ── Agents ───────────────────────────────────────────────────────────────

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
        root_path: str,
        grpc_port: int | None = None,
    ) -> Agent:
        group = await self.get_or_create_group(group_name)
        port = grpc_port or await self._next_port()

        agent = Agent(
            agent_id=str(uuid.uuid4()),
            name=name,
            group_id=group.id,
            auth_token=generate_token(),
            grpc_port=port,
            root_path=root_path,
            status="stopped",
        )
        self._s.add(agent)
        await self._s.flush()
        await self._record_event(agent.agent_id, "agent_created", {"name": name})
        # Reload with relationships so callers can access .group safely
        return await self.get_agent(agent.agent_id)

    async def update_status(
        self,
        agent_id: str,
        status: str,
        pid: int | None = -1,
    ) -> Agent | None:
        agent = await self.get_agent(agent_id)  # already loads .group via selectinload
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

    # ── Dashboard helpers ─────────────────────────────────────────────────────

    async def dashboard_summary(self) -> dict:
        total_agents = await self._s.scalar(select(func.count()).select_from(Agent))
        running = await self._s.scalar(
            select(func.count()).select_from(Agent).where(Agent.status == "running")
        )
        total_groups = await self._s.scalar(
            select(func.count()).select_from(AgentGroup)
        )
        return {
            "total_agents": total_agents or 0,
            "running_agents": running or 0,
            "stopped_agents": (total_agents or 0) - (running or 0),
            "total_groups": total_groups or 0,
        }

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _next_port(self) -> int:
        result = await self._s.execute(select(func.max(Agent.grpc_port)))
        max_port = result.scalar_one_or_none()
        next_port = (max_port or _PORT_START - 1) + 1
        if next_port > _PORT_END:
            raise RuntimeError("gRPC port range exhausted")
        return next_port

    async def _record_event(
        self, agent_id: str, event_type: str, payload: dict
    ) -> None:
        event = AgentEvent(
            agent_id=agent_id, event_type=event_type, payload=payload
        )
        self._s.add(event)
        await self._s.flush()  # prevent autoflush-on-SELECT IntegrityErrors
