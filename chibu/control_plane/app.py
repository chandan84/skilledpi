"""FastAPI application factory for the Chibu Control Plane."""

from __future__ import annotations

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
    logger.info("Control plane ready")
    yield
    logger.info("Control plane shutting down …")
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

    from chibu.control_plane.routers import agents, chiboos, dashboard, ws

    app.include_router(dashboard.router)
    app.include_router(agents.router, prefix="/agents")
    app.include_router(chiboos.router, prefix="/chiboos")
    app.include_router(ws.router, prefix="/ws")

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
