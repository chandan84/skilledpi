"""Integration tests for AgentRegistry using an in-memory SQLite database."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from chibu.db.models import Agent, AgentEvent, AgentGroup, Base, PerformanceMetric
from chibu.registry.agent_registry import AgentRegistry


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s

    await engine.dispose()


@pytest.mark.asyncio
async def test_create_agent_assigns_unique_token(session, tmp_path):
    reg = AgentRegistry(session)
    agent = await reg.create_agent("pi", "badmono", str(tmp_path))
    assert len(agent.auth_token) == 40
    assert agent.auth_token.isalnum()


@pytest.mark.asyncio
async def test_agent_id_is_composite(session, tmp_path):
    reg = AgentRegistry(session)
    agent = await reg.create_agent("coder-1", "research", str(tmp_path))
    assert agent.agent_id == "research_coder-1"


@pytest.mark.asyncio
async def test_create_agent_creates_group_automatically(session, tmp_path):
    reg = AgentRegistry(session)
    agent = await reg.create_agent("pi", "new-chiboo", str(tmp_path))
    groups = await reg.list_groups()
    assert any(g.name == "new-chiboo" for g in groups)
    assert agent.group_id is not None


@pytest.mark.asyncio
async def test_two_agents_in_same_group_share_group_record(session, tmp_path):
    reg = AgentRegistry(session)
    a1 = await reg.create_agent("agent-a", "shared", str(tmp_path / "a"))
    a2 = await reg.create_agent("agent-b", "shared", str(tmp_path / "b"))
    assert a1.group_id == a2.group_id


@pytest.mark.asyncio
async def test_agents_get_ascending_ports(session, tmp_path):
    reg = AgentRegistry(session)
    a1 = await reg.create_agent("first", "g", str(tmp_path / "1"))
    a2 = await reg.create_agent("second", "g", str(tmp_path / "2"))
    assert a2.grpc_port == a1.grpc_port + 1


@pytest.mark.asyncio
async def test_update_status_persists(session, tmp_path):
    reg = AgentRegistry(session)
    agent = await reg.create_agent("pi", "g", str(tmp_path))
    updated = await reg.update_status(agent.agent_id, "running", pid=12345)
    assert updated.status == "running"
    assert updated.pid == 12345


@pytest.mark.asyncio
async def test_delete_agent(session, tmp_path):
    reg = AgentRegistry(session)
    agent = await reg.create_agent("temp", "g", str(tmp_path))
    deleted = await reg.delete_agent(agent.agent_id)
    assert deleted is True
    assert await reg.get_agent(agent.agent_id) is None


@pytest.mark.asyncio
async def test_dashboard_summary_accuracy(session, tmp_path):
    reg = AgentRegistry(session)
    for i in range(3):
        a = await reg.create_agent(f"agent-{i}", "grp", str(tmp_path / str(i)))
        if i < 2:
            await reg.update_status(a.agent_id, "running", pid=1000 + i)

    summary = await reg.dashboard_summary()
    assert summary["total_agents"] == 3
    assert summary["running_agents"] == 2
    assert summary["stopped_agents"] == 1
    assert summary["total_chiboos"] == 1
