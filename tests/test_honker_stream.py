"""Durable stream tests using a file-backed SQLite DB."""

import asyncio

import pytest
import pytest_asyncio
import honker


@pytest_asyncio.fixture()
async def hdb(tmp_path):
    db = honker.open(str(tmp_path / "test.db"))
    db.queue("_bootstrap")  # Bootstrap honker schema
    yield db


@pytest.mark.asyncio
async def test_stream_publish_and_subscribe_from_zero(hdb):
    s = hdb.stream("agent_events")
    s.publish({"agent_id": "g_abc", "event_type": "agent_created", "payload": {}})
    s.publish({"agent_id": "g_abc", "event_type": "status_changed", "payload": {"status": "running"}})

    events = []
    async for ev in s.subscribe(from_offset=0):
        events.append(ev)
        if len(events) >= 2:
            break

    assert len(events) == 2
    assert events[0].payload["event_type"] == "agent_created"
    assert events[1].payload["event_type"] == "status_changed"
    assert events[0].offset < events[1].offset


@pytest.mark.asyncio
async def test_stream_consumer_resumes_from_saved_offset(hdb):
    s = hdb.stream("agent_events")
    s.publish({"agent_id": "g_abc", "event_type": "e1", "payload": {}})

    # Read first event and save offset
    first_event = None
    async for ev in s.subscribe(from_offset=0):
        first_event = ev
        break
    assert first_event is not None

    s.save_offset("consumer-1", first_event.offset)

    # Publish second event
    s.publish({"agent_id": "g_abc", "event_type": "e2", "payload": {}})

    # Resuming consumer-1 should skip the first event
    second_event = None
    async for ev in s.subscribe(consumer="consumer-1"):
        second_event = ev
        break
    assert second_event is not None
    assert second_event.payload["event_type"] == "e2"


@pytest.mark.asyncio
async def test_stream_get_offset_returns_zero_for_new_consumer(hdb):
    s = hdb.stream("agent_events")
    assert s.get_offset("brand-new-consumer") == 0


@pytest.mark.asyncio
async def test_stream_multiple_consumers_independent_offsets(hdb):
    s = hdb.stream("agent_events")
    s.publish({"agent_id": "a", "event_type": "created", "payload": {}})
    s.publish({"agent_id": "b", "event_type": "created", "payload": {}})

    # Consumer 1 reads both events
    c1_events = []
    async for ev in s.subscribe(from_offset=0):
        c1_events.append(ev)
        if len(c1_events) >= 2:
            break
    s.save_offset("c1", c1_events[-1].offset)

    # Consumer 2 reads only from the start — independent offset
    c2_events = []
    async for ev in s.subscribe(from_offset=0):
        c2_events.append(ev)
        if len(c2_events) >= 2:
            break

    assert len(c1_events) == 2
    assert len(c2_events) == 2
    assert s.get_offset("c1") == c1_events[-1].offset
    assert s.get_offset("c2") == 0  # c2 never saved its offset


@pytest.mark.asyncio
async def test_stream_agent_id_filter_in_events_endpoint(hdb):
    s = hdb.stream("agent_events")
    s.publish({"agent_id": "g_foo", "event_type": "agent_created", "payload": {}})
    s.publish({"agent_id": "g_bar", "event_type": "agent_created", "payload": {}})
    s.publish({"agent_id": "g_foo", "event_type": "status_changed", "payload": {"status": "running"}})

    foo_events = []
    async for ev in s.subscribe(from_offset=0):
        if ev.payload.get("agent_id") == "g_foo":
            foo_events.append(ev)
        if ev.offset >= 3:
            break

    assert len(foo_events) == 2
    assert all(e.payload["agent_id"] == "g_foo" for e in foo_events)
