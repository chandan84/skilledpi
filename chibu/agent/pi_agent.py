"""PiAgent — manages a persistent `pi --mode rpc` subprocess.

Each agent instance owns exactly one pi process.  The process is started via
start() and stopped via stop().  execute() sends commands over stdin and yields
AgentEvent objects parsed from the JSON event stream on stdout.

No Anthropic SDK.  No direct LLM calls.  All intelligence lives inside pi.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("chibu.agent")

_DEFAULT_MODEL = "faah"
_VALID_MODELS = {"staah", "faah"}
_PI_READY_TIMEOUT = 15.0  # seconds to wait for pi to emit its first event after start


@dataclass
class AgentEvent:
    event_type: str  # text | tool_use | tool_update | tool_result | agent_start | done | error
    content: str = ""
    tool_name: str = ""
    is_done: bool = False
    metadata: dict = field(default_factory=dict)


class PiAgent:
    """A single Chibu pi agent backed by a `pi --mode rpc` subprocess."""

    def __init__(
        self,
        agent_id: str,
        name: str,
        chiboo: str,
        auth_token: str,
        workspace: Path,
        grpc_port: int = 0,
    ) -> None:
        self.agent_id = agent_id
        self.name = name
        self.chiboo = chiboo
        self.auth_token = auth_token
        self.workspace = workspace
        self.grpc_port = grpc_port

        self._proc: asyncio.subprocess.Process | None = None
        self._busy = False
        self._status = "stopped"
        self._current_session_id = ""

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            logger.debug("Agent %s already running (pid=%d)", self.name, self._proc.pid)
            return

        self.workspace.mkdir(parents=True, exist_ok=True)

        self._proc = await asyncio.create_subprocess_exec(
            "pi", "--mode", "rpc",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workspace),
        )
        self._status = "ready"
        self._busy = False
        logger.info(
            "Pi agent '%s' started  pid=%d  workspace=%s",
            self.name,
            self._proc.pid,
            self.workspace,
        )

    async def stop(self) -> None:
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        self._status = "stopped"
        self._busy = False
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        logger.info("Pi agent '%s' stopped", self.name)

    async def restart(self) -> None:
        await self.stop()
        await self.start()
        logger.info("Pi agent '%s' restarted", self.name)

    # ── auth ──────────────────────────────────────────────────────────────────

    def verify_token(self, token: str) -> bool:
        return token == self.auth_token

    # ── execution ─────────────────────────────────────────────────────────────

    async def execute(
        self,
        prompt: str,
        model: str = "",
        new_session: bool = False,
        compact_first: bool = False,
        files: list[str] | None = None,
        timeout_seconds: int = 120,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Send a prompt to the pi subprocess and stream back events."""
        if self._proc is None or self._proc.returncode is not None:
            yield AgentEvent(event_type="error", content="Agent not running", is_done=True)
            return

        if self._busy:
            yield AgentEvent(event_type="error", content="Agent busy — try again", is_done=True)
            return

        self._busy = True
        effective_timeout = timeout_seconds if timeout_seconds > 0 else 120
        effective_model = model.strip() if model.strip() in _VALID_MODELS else _DEFAULT_MODEL

        try:
            await self._send({"command": "set_model", "model": effective_model})

            if compact_first:
                await self._send({"command": "compact"})
                await asyncio.sleep(0.1)

            if new_session:
                await self._send({"command": "new_session"})
                await asyncio.sleep(0.1)

            full_prompt = _build_prompt(prompt, files)
            await self._send({"command": "prompt", "prompt": full_prompt})

            deadline = asyncio.get_event_loop().time() + effective_timeout

            async for event in self._read_events(deadline):
                yield event
                if event.is_done:
                    return

        except Exception as exc:  # noqa: BLE001
            logger.exception("Execute error on agent %s: %s", self.name, exc)
            yield AgentEvent(event_type="error", content=str(exc), is_done=True)
        finally:
            self._busy = False

    # ── skills introspection ──────────────────────────────────────────────────

    def list_skills(self) -> list[dict]:
        skills_dir = self.workspace / ".pi" / "skills"
        if not skills_dir.exists():
            return []
        result = []
        for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                meta = _parse_skill_md(skill_md)
                result.append({
                    "name": skill_dir.name,
                    "description": meta.get("description", ""),
                    "license": meta.get("license", ""),
                })
        return result

    # ── info ──────────────────────────────────────────────────────────────────

    def info(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "chiboo": self.chiboo,
            "status": self._status,
            "pid": self._proc.pid if self._proc else None,
            "busy": self._busy,
            "skills": [s["name"] for s in self.list_skills()],
        }

    # ── private helpers ───────────────────────────────────────────────────────

    async def _send(self, command: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            return
        line = json.dumps(command) + "\n"
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

    async def _read_events(
        self, deadline: float
    ) -> AsyncGenerator[AgentEvent, None]:
        assert self._proc is not None
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                yield AgentEvent(
                    event_type="error",
                    content=f"Timeout after {deadline:.0f}s",
                    is_done=True,
                )
                return

            try:
                raw = await asyncio.wait_for(
                    self._proc.stdout.readline(),
                    timeout=min(remaining, 30.0),
                )
            except asyncio.TimeoutError:
                yield AgentEvent(
                    event_type="error",
                    content="Timed out waiting for pi response",
                    is_done=True,
                )
                return

            if not raw:
                yield AgentEvent(
                    event_type="error",
                    content="Pi process closed stdout",
                    is_done=True,
                )
                return

            line = raw.decode(errors="replace").strip()
            if not line:
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("Non-JSON stdout from pi: %s", line[:120])
                continue

            ev = _map_pi_event(payload)

            if ev.event_type == "agent_start":
                self._current_session_id = payload.get("sessionId", "")

            yield ev

            if ev.is_done:
                return


# ── event mapping ─────────────────────────────────────────────────────────────

def _map_pi_event(payload: dict) -> AgentEvent:
    """Translate a raw pi RPC JSON event into an AgentEvent."""
    # pi uses camelCase event type names on the wire
    t = payload.get("type", "")

    # Normalise to snake_case for matching
    t_norm = _camel_to_snake(t)

    if t_norm in ("message_update", "message_delta"):
        content = payload.get("content", "")
        if isinstance(content, list):
            content = "".join(
                c.get("text", "") for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            )
        return AgentEvent(event_type="text", content=str(content))

    if t_norm == "tool_execution_start":
        return AgentEvent(
            event_type="tool_use",
            tool_name=payload.get("toolName", payload.get("tool_name", "")),
            content=json.dumps(payload.get("input", {})),
        )

    if t_norm == "tool_execution_update":
        return AgentEvent(
            event_type="tool_update",
            tool_name=payload.get("toolName", payload.get("tool_name", "")),
            content=str(payload.get("update", payload.get("content", ""))),
        )

    if t_norm == "tool_execution_end":
        result = payload.get("result", payload.get("output", ""))
        if isinstance(result, (dict, list)):
            result = json.dumps(result)
        return AgentEvent(
            event_type="tool_result",
            tool_name=payload.get("toolName", payload.get("tool_name", "")),
            content=str(result),
        )

    if t_norm == "agent_end":
        return AgentEvent(event_type="done", is_done=True)

    if t_norm == "agent_start":
        return AgentEvent(event_type="agent_start", content="")

    if t_norm == "error":
        return AgentEvent(
            event_type="error",
            content=payload.get("message", payload.get("error", str(payload))),
            is_done=True,
        )

    # Pass-through for any other events (context, compaction, etc.)
    return AgentEvent(
        event_type=t_norm or "unknown",
        content=json.dumps(payload),
        metadata=payload,
    )


def _camel_to_snake(name: str) -> str:
    """agentStart → agent_start, toolExecutionEnd → tool_execution_end."""
    import re
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _build_prompt(prompt: str, files: list[str] | None) -> str:
    if not files:
        return prompt
    refs = " ".join(f"@{f}" for f in files)
    return f"{refs} {prompt}"


def _parse_skill_md(path: Path) -> dict:
    """Extract YAML frontmatter from a SKILL.md file."""
    try:
        import re
        text = path.read_text(errors="replace")
        m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        if not m:
            return {}
        import yaml
        return yaml.safe_load(m.group(1)) or {}
    except Exception:  # noqa: BLE001
        return {}
