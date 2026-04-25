"""Background asyncio workers: snapshot queue, reload queue, log watchdog."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

logger = logging.getLogger("chibu.honker.workers")

_stop_event: asyncio.Event | None = None


def get_stop_event() -> asyncio.Event:
    global _stop_event
    if _stop_event is None:
        _stop_event = asyncio.Event()
    return _stop_event


async def run_snapshot_worker(pm) -> None:
    """Drain the snapshot queue, writing the registry snapshot file on each job."""
    from chibu.db.engine import get_session_factory
    from chibu.registry.agent_registry import AgentRegistry
    from chibu.control_plane.routers.agents import _agent_record
    from ._queues import snapshot_queue

    worker_id = f"snapshot-{uuid.uuid4().hex[:8]}"
    q = snapshot_queue()
    stop = get_stop_event()

    try:
        async for job in q.claim(worker_id, idle_poll_s=5.0):
            if stop.is_set():
                job.retry(delay_s=0, error="worker stopping")
                break
            try:
                factory = get_session_factory()
                async with factory() as session:
                    registry = AgentRegistry(session)
                    agents = await registry.list_agents()
                    pm.write_registry_snapshot([_agent_record(a) for a in agents])
                job.ack()
                logger.debug("Registry snapshot flushed")
            except Exception as exc:
                delay = 5 * (2 ** (job.attempts - 1))
                job.retry(delay_s=delay, error=str(exc))
                logger.warning("Snapshot flush failed (attempt %d): %s", job.attempts, exc)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("Snapshot worker crashed: %s", exc)


async def run_reload_worker() -> None:
    """Drain the hot-reload queue, calling gRPC Reload with retries."""
    from ._queues import reload_queue

    worker_id = f"reload-{uuid.uuid4().hex[:8]}"
    q = reload_queue()
    stop = get_stop_event()

    try:
        async for job in q.claim(worker_id, idle_poll_s=5.0):
            if stop.is_set():
                job.retry(delay_s=0, error="worker stopping")
                break
            payload = job.payload
            try:
                from chibu.grpc_server.client import ChibuClient
                async with ChibuClient(
                    "127.0.0.1",
                    payload["grpc_port"],
                    payload["auth_token"],
                    timeout=10.0,
                ) as client:
                    ok = await client.reload()
                if ok:
                    job.ack()
                    logger.info("Hot-reload succeeded for agent %s", payload.get("agent_id"))
                else:
                    delay = 2 * (2 ** (job.attempts - 1))
                    job.retry(delay_s=delay, error="Reload returned ok=false")
                    logger.warning(
                        "Hot-reload returned ok=false for %s (attempt %d)",
                        payload.get("agent_id"), job.attempts,
                    )
            except Exception as exc:
                delay = 2 * (2 ** (job.attempts - 1))
                job.retry(delay_s=delay, error=str(exc))
                logger.warning(
                    "Hot-reload failed for %s (attempt %d): %s",
                    payload.get("agent_id"), job.attempts, exc,
                )
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("Reload worker crashed: %s", exc)


async def run_log_watchdog(
    agent_id: str, log_path: Path, stop_event: asyncio.Event
) -> None:
    """Watch agent.log for new bytes and fire a Honker notify for each change."""
    from ._db import get_hdb
    from ._channels import LOG_WRITTEN

    channel = f"{LOG_WRITTEN}:{agent_id}"
    db = get_hdb()
    offset = log_path.stat().st_size if log_path.exists() else 0

    try:
        while not stop_event.is_set():
            await asyncio.sleep(0.1)
            if not log_path.exists():
                continue
            size = log_path.stat().st_size
            if size <= offset:
                continue
            with db.transaction() as tx:
                tx.notify(channel, {"offset": offset, "size": size})
            offset = size
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning("Log watchdog for %s crashed: %s", agent_id, exc)


async def run_notification_pruner() -> None:
    """Periodically prune stale Honker notifications to prevent unbounded growth."""
    from ._db import get_hdb

    stop = get_stop_event()
    try:
        while not stop.is_set():
            await asyncio.sleep(60)
            try:
                removed = get_hdb().prune_notifications(older_than_s=120)
                if removed:
                    logger.debug("Pruned %d stale notifications", removed)
            except Exception as exc:
                logger.warning("Notification pruning failed: %s", exc)
    except asyncio.CancelledError:
        pass
