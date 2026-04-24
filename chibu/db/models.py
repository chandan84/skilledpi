"""SQLAlchemy ORM models for the Chibu platform.

All tables use UUID primary keys stored as strings for SQLite ↔ PostgreSQL
portability.  Timestamps are stored as UTC ISO strings (SQLite) or proper
TIMESTAMP WITH TIME ZONE (PostgreSQL); SQLAlchemy handles the mapping.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
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


# ── Agent Groups ──────────────────────────────────────────────────────────────


class AgentGroup(Base):
    __tablename__ = "agent_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )

    agents: Mapped[list[Agent]] = relationship(
        "Agent", back_populates="group", lazy="raise"
    )


# ── Agents ────────────────────────────────────────────────────────────────────


class Agent(Base):
    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_groups.id"), nullable=False
    )
    auth_token: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    grpc_port: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    root_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="stopped"
    )  # stopped | starting | running | error
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    group: Mapped[AgentGroup] = relationship(
        "AgentGroup", back_populates="agents", lazy="raise"
    )
    sessions: Mapped[list[AgentSession]] = relationship(
        "AgentSession", back_populates="agent", lazy="noload"
    )
    metrics: Mapped[list[PerformanceMetric]] = relationship(
        "PerformanceMetric", back_populates="agent", lazy="noload"
    )

    __table_args__ = (UniqueConstraint("name", "group_id", name="uq_agent_name_group"),)


# ── Sessions ──────────────────────────────────────────────────────────────────


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.agent_id"), nullable=False
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_id: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(
        String(20), default="running"
    )  # running | completed | error
    tool_calls_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    agent: Mapped[Agent] = relationship("Agent", back_populates="sessions")


# ── LLM Requests (analysis) ───────────────────────────────────────────────────


class LLMRequest(Base):
    __tablename__ = "llm_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    stop_reason: Mapped[str] = mapped_column(String(40), default="")
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )


# ── Performance Metrics ───────────────────────────────────────────────────────


class PerformanceMetric(Base):
    __tablename__ = "performance_metrics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.agent_id"), nullable=False, index=True
    )
    metric_name: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    tags: Mapped[dict] = mapped_column(JSON, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )

    agent: Mapped[Agent] = relationship("Agent", back_populates="metrics")


# ── Agent Events (audit log) ──────────────────────────────────────────────────


class AgentEvent(Base):
    __tablename__ = "agent_events"

    # Integer maps to ROWID on SQLite (auto-increment); on PostgreSQL it becomes BIGSERIAL.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
