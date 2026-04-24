"""SQLAlchemy ORM models for the Chibu platform."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ── Chiboos (agent groups) ────────────────────────────────────────────────────


class AgentGroup(Base):
    """A chiboo — a named group of pi agents."""

    __tablename__ = "agent_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    agents: Mapped[list["Agent"]] = relationship(
        "Agent", back_populates="group", lazy="raise"
    )


# ── Agents ────────────────────────────────────────────────────────────────────


class Agent(Base):
    __tablename__ = "agents"

    # Composite human-readable ID: "{chiboo_name}_{agent_name}"
    agent_id: Mapped[str] = mapped_column(String(240), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_groups.id"), nullable=False
    )
    auth_token: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    grpc_port: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    # workspace_path is the directory where pi --mode rpc runs (.pi/ lives here)
    workspace_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="stopped"
    )  # stopped | starting | running | error
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    group: Mapped[AgentGroup] = relationship(
        "AgentGroup", back_populates="agents", lazy="raise"
    )

    __table_args__ = (UniqueConstraint("name", "group_id", name="uq_agent_name_group"),)


# ── Agent Events (audit log) ──────────────────────────────────────────────────


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


# ── Performance Metrics ───────────────────────────────────────────────────────


class PerformanceMetric(Base):
    __tablename__ = "performance_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    tags: Mapped[dict] = mapped_column(JSON, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
