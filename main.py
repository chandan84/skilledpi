"""Chibu Control Plane — main entrypoint.

Usage:
  python main.py [--host HOST] [--port PORT] [--reload]
"""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn


def main() -> None:
    logging.basicConfig(
        level=os.getenv("CHIBU_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)-28s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Chibu Control Plane")
    parser.add_argument("--host", default=os.getenv("CHIBU_CONTROL_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CHIBU_CONTROL_PORT", "8000")))
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    print(f"  χ  Chibu Control Plane v0.1.0 — badmono org")
    print(f"     http://{args.host}:{args.port}")
    print()

    uvicorn.run(
        "chibu.control_plane.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
        log_level=os.getenv("CHIBU_LOG_LEVEL", "info").lower(),
        access_log=True,
    )


if __name__ == "__main__":
    main()
