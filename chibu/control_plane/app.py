"""FastAPI application factory for the Chibu Control Plane."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from chibu.db.engine import dispose_engine, init_db

logger = logging.getLogger("chibu.control_plane")

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warn loudly if ANTHROPIC_API_KEY is missing — agents will fail at execute time
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.warning(
            "ANTHROPIC_API_KEY is not set. Pi agents will fail when executing prompts."
        )

    # Guard against multi-worker deployments that break in-memory process tracking
    workers = int(os.getenv("WEB_CONCURRENCY", "1"))
    if workers > 1:
        raise RuntimeError(
            f"Chibu control plane must run with a single uvicorn worker "
            f"(WEB_CONCURRENCY={workers} detected). "
            "In-memory process tracking is not safe across multiple worker processes."
        )

    logger.info("Initialising database …")
    await init_db()

    # Reconcile in-memory process state with DB after a restart.
    # Agents the DB believes are "running" either still have a live PID
    # (we track them as orphans) or their process died (mark stopped).
    from chibu.control_plane.deps import get_process_manager
    from chibu.db.engine import get_session_factory
    from chibu.registry.agent_registry import AgentRegistry

    pm = get_process_manager()
    factory = get_session_factory()
    async with factory() as session:
        registry = AgentRegistry(session)
        running = await registry.list_agents()
        running_records = [
            {"agent_id": a.agent_id, "pid": a.pid, "status": a.status}
            for a in running
            if a.status == "running"
        ]
        stale_ids = await pm.recover_running_agents(running_records)
        for agent_id in stale_ids:
            await registry.update_status(agent_id, "stopped", pid=None)
        await session.commit()

    # Initialise Honker (SQLite pub/sub + work queues) when enabled
    honker_enabled = os.getenv("CHIBU_HONKER_ENABLED", "true").lower() not in ("false", "0", "no")
    worker_tasks: list = []

    if honker_enabled:
        from chibu.honker import init_honker
        from chibu.honker._workers import (
            get_stop_event,
            run_notification_pruner,
            run_reload_worker,
            run_snapshot_worker,
        )

        init_honker()
        logger.info("Honker database initialised")

        worker_tasks = [
            asyncio.create_task(run_snapshot_worker(pm), name="honker-snapshot"),
            asyncio.create_task(run_reload_worker(), name="honker-reload"),
            asyncio.create_task(run_notification_pruner(), name="honker-pruner"),
        ]

    logger.info("Control plane ready")
    yield
    logger.info("Control plane shutting down …")

    if honker_enabled and worker_tasks:
        get_stop_event().set()
        for t in worker_tasks:
            t.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)

    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Chibu Control Plane",
        version="0.1.0",
        description="Pi Agent Platform — badmono org",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    from chibu.control_plane.routers import agents, chiboos, dashboard, events, ws

    app.include_router(dashboard.router)
    app.include_router(agents.router, prefix="/agents")
    app.include_router(chiboos.router, prefix="/chiboos")
    app.include_router(ws.router, prefix="/ws")
    app.include_router(events.router, prefix="/events")

    @app.get("/health", tags=["health"])
    async def health():
        """Liveness probe — checks DB connectivity."""
        try:
            from chibu.db.engine import get_session_factory
            from sqlalchemy import text
            factory = get_session_factory()
            async with factory() as session:
                await session.execute(text("SELECT 1"))
            db_status = "ok"
        except Exception as exc:  # noqa: BLE001
            logger.error("Health check DB error: %s", exc)
            db_status = "error"

        status_code = 200 if db_status == "ok" else 503
        return JSONResponse(
            {"status": "ok" if db_status == "ok" else "degraded", "db": db_status},
            status_code=status_code,
        )

    return app
