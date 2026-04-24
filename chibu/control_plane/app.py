"""FastAPI application factory for the Chibu Control Plane."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from chibu.db.engine import dispose_engine, init_db

logger = logging.getLogger("chibu.control_plane")

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI):
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
        description="Pi Agent Platform by badmono org",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    # ── static files ──────────────────────────────────────────────────────────
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ── routers ───────────────────────────────────────────────────────────────
    from chibu.control_plane.routers import agents, groups, dashboard, ws

    app.include_router(dashboard.router)
    app.include_router(agents.router, prefix="/agents")
    app.include_router(groups.router, prefix="/groups")
    app.include_router(ws.router, prefix="/ws")

    return app
