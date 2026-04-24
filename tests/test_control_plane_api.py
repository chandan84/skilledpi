"""Integration tests for the FastAPI control plane using a real in-memory DB."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from chibu.control_plane.app import create_app
from chibu.db.engine import get_session
from chibu.db.models import (  # noqa: F401
    Agent,
    AgentEvent,
    AgentGroup,
    AgentSession,
    Base,
    LLMRequest,
    PerformanceMetric,
)


def _make_engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest_asyncio.fixture()
async def _test_db(tmp_path):
    """Shared in-memory DB engine + session factory for one test."""
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    yield engine, factory
    await engine.dispose()


@pytest_asyncio.fixture()
async def client(tmp_path, _test_db):
    """FastAPI test client with an isolated in-memory database."""
    engine, factory = _test_db

    async def _override_session():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    import os
    os.environ["CHIBU_AGENTS_DIR"] = str(tmp_path / "agents")
    os.environ["CHIBU_REGISTRY_SNAPSHOT"] = str(tmp_path / "registry.json")

    from chibu.control_plane import deps
    deps.get_process_manager.cache_clear()

    app = create_app()
    app.dependency_overrides[get_session] = _override_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c

    deps.get_process_manager.cache_clear()


@pytest.mark.asyncio
async def test_list_agents_empty(client):
    r = await client.get("/agents/")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_create_agent_returns_record(client):
    r = await client.post(
        "/agents/",
        json={"name": "test-pi", "agent_group": "badmono org"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "test-pi"
    assert len(body["auth_token"]) == 40
    assert body["auth_token"].isalnum()
    assert body["status"] == "stopped"
    assert body["grpc_port"] >= 50051


@pytest.mark.asyncio
async def test_list_groups_after_create(client):
    await client.post("/agents/", json={"name": "pi1", "agent_group": "group-a"})
    await client.post("/agents/", json={"name": "pi2", "agent_group": "group-a"})
    r = await client.get("/groups/")
    assert r.status_code == 200
    groups = r.json()
    assert any(g["name"] == "group-a" for g in groups)
    matching = next(g for g in groups if g["name"] == "group-a")
    assert matching["agent_count"] == 2


@pytest.mark.asyncio
async def test_delete_stopped_agent(client):
    r = await client.post("/agents/", json={"name": "to-delete", "agent_group": "g"})
    agent_id = r.json()["agent_id"]

    del_r = await client.delete(f"/agents/{agent_id}")
    assert del_r.status_code == 200
    assert del_r.json()["ok"] is True

    list_r = await client.get("/agents/")
    assert all(a["agent_id"] != agent_id for a in list_r.json())


@pytest.mark.asyncio
async def test_cannot_delete_running_agent(client, _test_db):
    r = await client.post("/agents/", json={"name": "live", "agent_group": "g"})
    assert r.status_code == 200
    agent_id = r.json()["agent_id"]

    _engine, factory = _test_db
    async with factory() as session:
        from chibu.registry.agent_registry import AgentRegistry
        reg = AgentRegistry(session)
        await reg.update_status(agent_id, "running", pid=99999)
        await session.commit()

    del_r = await client.delete(f"/agents/{agent_id}")
    assert del_r.status_code == 409


@pytest.mark.asyncio
async def test_api_summary_returns_counts(client):
    await client.post("/agents/", json={"name": "a1", "agent_group": "g"})
    await client.post("/agents/", json={"name": "a2", "agent_group": "g"})
    r = await client.get("/api/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_agents"] == 2
    assert body["total_groups"] == 1
