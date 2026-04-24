"""AgentProcessManager — starts and stops Pi agent gRPC server subprocesses.

Each agent subprocess runs chibu.grpc_server.server, which in turn spawns
`pi --mode rpc`.  This manager only knows about the gRPC server process;
the pi subprocess lifetime is handled inside the server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

logger = logging.getLogger("chibu.process.manager")

_READY_POLL_INTERVAL = 0.5
_READY_TIMEOUT = 30.0
_STOP_GRACE = 5.0


class AgentProcessManager:
    def __init__(self, agents_dir: Path, registry_snapshot_path: Path) -> None:
        self.agents_dir = agents_dir
        self.registry_snapshot = registry_snapshot_path
        self._procs: dict[str, asyncio.subprocess.Process] = {}

    # ── start ─────────────────────────────────────────────────────────────────

    async def start(self, agent_record: dict) -> int:
        agent_id = agent_record["agent_id"]
        port = agent_record["grpc_port"]

        log_path = Path(agent_record["workspace_path"]) / "agent.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(log_path, "a")  # noqa: SIM115

        cmd = [
            sys.executable, "-m", "chibu.grpc_server.server",
            "--agent-id", agent_id,
            "--port", str(port),
            "--agents-dir", str(self.agents_dir),
            "--registry", str(self.registry_snapshot),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=log_fh,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )

        self._procs[agent_id] = proc
        logger.info("Spawned agent %s pid=%d port=%d", agent_record["name"], proc.pid, port)

        asyncio.create_task(
            self._wait_ready(agent_id, port, proc),
            name=f"ready-{agent_id}",
        )

        return proc.pid

    # ── stop ──────────────────────────────────────────────────────────────────

    async def stop(self, agent_id: str, pid: int | None = None) -> None:
        proc = self._procs.pop(agent_id, None)
        if proc is not None:
            await self._terminate(proc)
            return

        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                await asyncio.sleep(_STOP_GRACE)
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass

    # ── readiness ─────────────────────────────────────────────────────────────

    async def _wait_ready(
        self, agent_id: str, port: int, proc: asyncio.subprocess.Process
    ) -> bool:
        from chibu.grpc_server.client import ChibuClient

        deadline = asyncio.get_event_loop().time() + _READY_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            if proc.returncode is not None:
                logger.error("Agent %s exited prematurely (rc=%d)", agent_id, proc.returncode)
                return False
            try:
                async with ChibuClient("127.0.0.1", port, "") as c:
                    if await c.ping():
                        logger.info("Agent %s ready on port %d", agent_id, port)
                        return True
            except Exception:
                pass
            await asyncio.sleep(_READY_POLL_INTERVAL)

        logger.warning("Agent %s readiness timeout", agent_id)
        return False

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=_STOP_GRACE)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    def write_registry_snapshot(self, agents: list[dict]) -> None:
        self.registry_snapshot.parent.mkdir(parents=True, exist_ok=True)
        self.registry_snapshot.write_text(json.dumps({"agents": agents}, indent=2))

    def is_running(self, agent_id: str) -> bool:
        proc = self._procs.get(agent_id)
        return proc is not None and proc.returncode is None
