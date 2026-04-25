"""Chibu Pi Agent gRPC server — entry point for each agent subprocess.

Each agent process:
  1. Loads its record from the registry snapshot written by the control plane.
  2. Bootstraps the workspace .pi/ directory if needed.
  3. Starts a PiAgent (spawns `pi --mode rpc` in the workspace).
  4. Serves a gRPC endpoint for Execute / GetInfo / ListSkills / Reload / Ping.
  5. Shuts down cleanly on SIGTERM / SIGINT.

Invoked by AgentProcessManager as:
  python -m chibu.grpc_server.server \
      --agent-id <id> --port <port> \
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


def _install_uvloop() -> None:
    try:
        import uvloop
        uvloop.install()
        logger.debug("uvloop installed")
    except ImportError:
        logger.debug("uvloop not available — using default asyncio loop")


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
        logger.error("Generated proto files missing — run: python -m grpc_tools.protoc ...")
        sys.exit(1)

    from chibu.agent.pi_agent import PiAgent
    from chibu.grpc_server.servicer import build_servicer
    from chibu.otel.tracing import init_otel
    from chibu.utils.filesystem import bootstrap_agent_root

    agent_rec = _load_agent_record(registry_path, agent_id)

    workspace = Path(agent_rec["workspace_path"])
    if not workspace.exists():
        bootstrap_agent_root(workspace, agent_id, agent_rec["name"], agent_rec.get("chiboo", ""))

    agent = PiAgent(
        agent_id=agent_id,
        name=agent_rec["name"],
        chiboo=agent_rec.get("chiboo", ""),
        auth_token=agent_rec["auth_token"],
        workspace=workspace,
        grpc_port=port,
    )

    # Start the pi subprocess before opening the gRPC port
    await agent.start()

    # Initialise OpenTelemetry (no-op if CHIBU_OTEL_ENABLED is unset/false)
    init_otel(agent_rec.get("name", agent_id))

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
        "Agent '%s' ready — gRPC %s  [pi pid=%s]",
        agent_rec["name"],
        listen_addr,
        agent._proc.pid if agent._proc else "?",
    )

    # Write a sentinel file so the control plane detects readiness via stat
    # instead of polling gRPC pings.
    _write_ready_sentinel(workspace, port)

    stop_event = asyncio.Event()

    def _handle_signal(sig, _frame):
        logger.info("Signal %s — shutting down", sig)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    await stop_event.wait()

    logger.info("Stopping agent '%s' …", agent_rec["name"])
    await agent.stop()
    await server.stop(grace=3)

    # Flush any buffered OTEL spans/metrics before the process exits
    try:
        from opentelemetry import trace
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=3000)
    except Exception:  # noqa: BLE001
        pass

    logger.info("Agent '%s' shut down cleanly", agent_rec["name"])


def _write_ready_sentinel(workspace: Path, port: int) -> None:
    """Create a file the control plane watches to confirm gRPC is accepting connections."""
    try:
        sentinel = workspace / f".agent_ready_{port}"
        sentinel.write_text(str(os.getpid()))
    except Exception:
        pass


def _load_agent_record(registry_path: Path, agent_id: str) -> dict:
    if not registry_path.exists():
        logger.error("Registry not found: %s", registry_path)
        sys.exit(1)
    try:
        data = json.loads(registry_path.read_text())
    except json.JSONDecodeError as exc:
        logger.error("Registry JSON invalid: %s", exc)
        sys.exit(1)

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
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--agents-dir", default="agents")
    parser.add_argument("--registry", default="chibu_registry.json")
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
