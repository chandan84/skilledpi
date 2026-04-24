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
from typing import Any

logger = logging.getLogger("chibu.process.manager")

_READY_POLL_INTERVAL = 0.5
_READY_TIMEOUT = 30.0
_STOP_GRACE = 5.0


def _pid_alive(pid: int) -> bool:
    """Return True if the OS process with *pid* is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


class AgentProcessManager:
    def __init__(self, agents_dir: Path, registry_snapshot_path: Path) -> None:
        self.agents_dir = agents_dir
        self.registry_snapshot = registry_snapshot_path
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._log_fhs: dict[str, Any] = {}
        # PIDs of agents that were running before our last restart (orphan tracking)
        self._orphan_pids: dict[str, int] = {}

    # ── start ─────────────────────────────────────────────────────────────────

    async def start(self, agent_record: dict) -> int:
        agent_id = agent_record["agent_id"]
        port = agent_record["grpc_port"]

        log_path = Path(agent_record["workspace_path"]) / "agent.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(log_path, "a")  # noqa: SIM115
        self._log_fhs[agent_id] = log_fh

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

        ready = await self._wait_ready(agent_id, port, proc)
        if not ready:
            logger.warning("Agent %s failed readiness check", agent_id)

        return proc.pid

    # ── recovery after control-plane restart ─────────────────────────────────

    async def recover_running_agents(self, agents: list[dict]) -> list[str]:
        """Reconcile in-memory state with DB after a control-plane restart.

        Agents the DB thinks are "running" either still have a live PID
        (orphan — we track it so is_running/stop work) or are actually dead
        (stale — caller should reset their DB status to "stopped").

        Returns the list of agent_ids that are stale.
        """
        stale: list[str] = []
        for rec in agents:
            agent_id = rec.get("agent_id", "")
            pid = rec.get("pid")
            if not pid:
                stale.append(agent_id)
                continue
            if _pid_alive(pid):
                self._orphan_pids[agent_id] = pid
                logger.info("Recovered orphan agent %s (pid=%d)", agent_id, pid)
            else:
                stale.append(agent_id)
                logger.info(
                    "Marking stale agent %s as stopped (pid=%d dead)", agent_id, pid
                )
        return stale

    # ── stop ──────────────────────────────────────────────────────────────────

    async def stop(self, agent_id: str, pid: int | None = None) -> None:
        fh = self._log_fhs.pop(agent_id, None)
        proc = self._procs.pop(agent_id, None)
        orphan_pid = self._orphan_pids.pop(agent_id, None)

        if proc is not None:
            await self._terminate(proc)
            if fh is not None:
                fh.close()
            return

        if fh is not None:
            fh.close()

        # Fall back to any known PID (orphan or caller-supplied)
        effective_pid = orphan_pid or pid
        if effective_pid:
            try:
                os.kill(effective_pid, signal.SIGTERM)
                await asyncio.sleep(_STOP_GRACE)
                try:
                    os.kill(effective_pid, signal.SIGKILL)
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
        tmp = self.registry_snapshot.with_suffix(".tmp")
        tmp.write_text(json.dumps({"agents": agents}, indent=2))
        tmp.replace(self.registry_snapshot)  # atomic on POSIX

    def is_running(self, agent_id: str) -> bool:
        proc = self._procs.get(agent_id)
        if proc is not None and proc.returncode is None:
            return True
        pid = self._orphan_pids.get(agent_id)
        if pid:
            if _pid_alive(pid):
                return True
            self._orphan_pids.pop(agent_id, None)
        return False
