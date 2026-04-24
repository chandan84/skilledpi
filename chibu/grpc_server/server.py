"""High-performance gRPC server entry point for a Chibu Pi agent subprocess.

Performance design:
  - Uses grpc.aio (asyncio-native) to avoid thread-per-call overhead.
  - Installs uvloop as the event loop policy for 2-4× faster I/O throughput.
  - Configures gRPC channel options for keepalive and flow control.
  - CHIBU_GRPC_WORKERS env var controls max concurrent RPCs (default: 100).

Invoked by AgentProcessManager as:
  python -m chibu.grpc_server.server \
      --agent-id <uuid> --port <port> \
      --agents-dir <path> --registry <path>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

logger = logging.getLogger("chibu.grpc.server")


def _install_uvloop() -> None:
    try:
        import uvloop

        uvloop.install()
        logger.debug("uvloop event loop installed")
    except ImportError:
        logger.debug("uvloop not available — using default asyncio event loop")


# gRPC channel / server tuning constants
_GRPC_OPTIONS = [
    ("grpc.keepalive_time_ms", 20_000),
    ("grpc.keepalive_timeout_ms", 5_000),
    ("grpc.keepalive_permit_without_calls", 1),
    ("grpc.http2.max_pings_without_data", 0),
    ("grpc.http2.min_time_between_pings_ms", 10_000),
    ("grpc.max_send_message_length", 32 * 1024 * 1024),
    ("grpc.max_receive_message_length", 32 * 1024 * 1024),
    ("grpc.so_reuseport", 1),
]


async def serve(
    agent_id: str,
    port: int,
    agents_dir: Path,
    registry_path: Path,
) -> None:
    from grpc import aio

    try:
        from chibu.grpc_server import chibu_agent_pb2_grpc
    except ImportError:
        logger.error("Generated proto files missing. Run: python generate_proto.py")
        sys.exit(1)

    from chibu.agent.pi_agent import PiAgent
    from chibu.grpc_server.servicer import build_servicer
    from chibu.utils.filesystem import bootstrap_agent_root

    agent_rec = _load_agent_record(registry_path, agent_id)

    root = Path(agent_rec["root_path"])
    if not root.exists():
        bootstrap_agent_root(root, agent_id, agent_rec["name"])

    agent = PiAgent(
        agent_id=agent_id,
        name=agent_rec["name"],
        agent_group=agent_rec["agent_group"],
        auth_token=agent_rec["auth_token"],
        root=root,
    )
    agent.load()

    max_concurrent = int(os.getenv("CHIBU_GRPC_WORKERS", "100"))
    server = aio.server(
        options=_GRPC_OPTIONS,
        maximum_concurrent_rpcs=max_concurrent,
    )

    servicer = build_servicer(agent)
    chibu_agent_pb2_grpc.add_ChiAgentServicer_to_server(servicer, server)

    listen_addr = f"0.0.0.0:{port}"
    server.add_insecure_port(listen_addr)
    await server.start()

    logger.info(
        "Pi agent '%s' ready — gRPC %s  [workers=%d]",
        agent_rec["name"],
        listen_addr,
        max_concurrent,
    )

    stop_event = asyncio.Event()

    def _handle_signal(sig, _frame):
        logger.info("Signal %s received — initiating graceful shutdown", sig)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    await stop_event.wait()
    logger.info("Stopping gRPC server (grace=3s) …")
    await server.stop(grace=3)
    logger.info("Agent '%s' shut down cleanly", agent_rec["name"])


def _load_agent_record(registry_path: Path, agent_id: str) -> dict:
    if not registry_path.exists():
        logger.error("Registry file not found: %s", registry_path)
        sys.exit(1)
    try:
        data = json.loads(registry_path.read_text())
    except json.JSONDecodeError as exc:
        logger.error("Registry JSON invalid: %s", exc)
        sys.exit(1)

    # Registry file written by control plane on every agent mutation
    for rec in data.get("agents", []):
        if rec.get("agent_id") == agent_id:
            return rec

    logger.error("Agent %s not found in registry %s", agent_id, registry_path)
    sys.exit(1)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("CHIBU_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)-28s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    parser = argparse.ArgumentParser(description="Chibu Pi Agent gRPC Server")
    parser.add_argument("--agent-id", required=True, help="Agent UUID")
    parser.add_argument("--port", type=int, required=True, help="gRPC listen port")
    parser.add_argument("--agents-dir", default="agents", help="Agents base directory")
    parser.add_argument(
        "--registry", default="chibu_registry.json", help="Registry snapshot path"
    )
    args = parser.parse_args()

    _install_uvloop()

    asyncio.run(
        serve(
            agent_id=args.agent_id,
            port=args.port,
            agents_dir=Path(args.agents_dir),
            registry_path=Path(args.registry),
        )
    )


if __name__ == "__main__":
    main()
