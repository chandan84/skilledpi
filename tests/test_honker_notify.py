"""Notify/listen tests using a file-backed SQLite DB."""

import asyncio

import pytest
import pytest_asyncio
import honker


@pytest_asyncio.fixture()
async def hdb(tmp_path):
    db = honker.open(str(tmp_path / "test.db"))
    # Bootstrap schema (queue creation creates _honker_notifications table)
    db.queue("_bootstrap")
    yield db


@pytest.mark.asyncio
async def test_notify_received_by_listener(hdb):
    listener = hdb.listen("agent:ready")
    with hdb.transaction() as tx:
        tx.notify("agent:ready", {"agent_id": "g_abc", "port": 50051})

    notif = await asyncio.wait_for(listener.__anext__(), timeout=3.0)
    assert notif.channel == "agent:ready"
    assert notif.payload["agent_id"] == "g_abc"
    assert notif.payload["port"] == 50051


@pytest.mark.asyncio
async def test_listener_only_receives_own_channel(hdb):
    listener = hdb.listen("channel-a")
    with hdb.transaction() as tx:
        tx.notify("channel-b", {"msg": "wrong"})
    with hdb.transaction() as tx:
        tx.notify("channel-a", {"msg": "right"})

    notif = await asyncio.wait_for(listener.__anext__(), timeout=3.0)
    assert notif.payload["msg"] == "right"


@pytest.mark.asyncio
async def test_multiple_notifications_delivered_in_order(hdb):
    listener = hdb.listen("agent:log:test-agent")
    for i in range(3):
        with hdb.transaction() as tx:
            tx.notify("agent:log:test-agent", {"offset": i * 100, "size": (i + 1) * 100})

    received = []
    for _ in range(3):
        notif = await asyncio.wait_for(listener.__anext__(), timeout=3.0)
        received.append(notif.payload["offset"])

    assert received == [0, 100, 200]


@pytest.mark.asyncio
async def test_log_channel_contains_agent_id(hdb):
    agent_id = "research_coder-1"
    channel = f"agent:log:{agent_id}"
    listener = hdb.listen(channel)

    with hdb.transaction() as tx:
        tx.notify(channel, {"offset": 0, "size": 512})

    notif = await asyncio.wait_for(listener.__anext__(), timeout=3.0)
    assert notif.channel == channel
    assert notif.payload["size"] == 512


@pytest.mark.asyncio
async def test_prune_notifications_removes_old_rows(hdb):
    with hdb.transaction() as tx:
        tx.notify("prune-test", {"msg": "old"})

    # max_keep=0 removes all notifications regardless of age
    removed = hdb.prune_notifications(max_keep=0)
    assert removed >= 1
