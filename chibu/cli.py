"""Chibu CLI — provision agents, connect via gRPC, manage the platform."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group()
def main():
    """χ Chibu — Pi Agent Platform CLI"""


# ── serve ─────────────────────────────────────────────────────────────────────


@main.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=8000, type=int)
@click.option("--reload", is_flag=True)
def serve(host, port, reload):
    """Start the Chibu Control Plane web server."""
    import uvicorn

    console.print(f"[bold violet]χ[/] Chibu Control Plane → http://{host}:{port}")
    uvicorn.run(
        "chibu.control_plane.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


# ── proto ─────────────────────────────────────────────────────────────────────


@main.command()
def proto():
    """Generate gRPC Python bindings from proto/chibu_agent.proto."""
    import subprocess

    result = subprocess.run(
        [sys.executable, "generate_proto.py"], capture_output=False
    )
    sys.exit(result.returncode)


# ── agent ─────────────────────────────────────────────────────────────────────


@main.group()
def agent():
    """Manage Pi agents from the command line."""


@agent.command("list")
def agent_list():
    """List all registered agents."""
    import httpx

    base = os.getenv("CHIBU_CONTROL_URL", "http://localhost:8000")
    try:
        agents = httpx.get(f"{base}/agents/").json()
    except Exception as e:
        console.print(f"[red]Error:[/] {e}")
        return

    t = Table(title="Pi Agents", show_lines=True)
    for col in ("Name", "Group", "Status", "Port", "Agent ID"):
        t.add_column(col, style="dim" if col == "Agent ID" else "")
    for a in agents:
        status_color = "green" if a["status"] == "running" else "red" if a["status"] == "error" else "grey50"
        t.add_row(
            a["name"],
            a.get("group_name", ""),
            f"[{status_color}]{a['status']}[/]",
            str(a["grpc_port"]),
            a["agent_id"][:8] + "…",
        )
    console.print(t)


@agent.command("start")
@click.argument("agent_id")
def agent_start(agent_id):
    """Start a Pi agent's gRPC server."""
    import httpx

    base = os.getenv("CHIBU_CONTROL_URL", "http://localhost:8000")
    r = httpx.post(f"{base}/agents/{agent_id}/start")
    if r.is_success:
        d = r.json()
        console.print(f"[green]✓[/] Agent {d.get('status')} (pid={d.get('pid')})")
    else:
        console.print(f"[red]✗[/] {r.json().get('detail', r.text)}")


@agent.command("stop")
@click.argument("agent_id")
def agent_stop(agent_id):
    """Stop a running Pi agent."""
    import httpx

    base = os.getenv("CHIBU_CONTROL_URL", "http://localhost:8000")
    r = httpx.post(f"{base}/agents/{agent_id}/stop")
    if r.is_success:
        console.print(f"[green]✓[/] Agent stopped")
    else:
        console.print(f"[red]✗[/] {r.json().get('detail', r.text)}")


@agent.command("delete")
@click.argument("agent_id")
@click.confirmation_option(prompt="Delete this agent permanently?")
def agent_delete(agent_id):
    """Delete a stopped agent and its registry record."""
    import httpx

    base = os.getenv("CHIBU_CONTROL_URL", "http://localhost:8000")
    r = httpx.delete(f"{base}/agents/{agent_id}")
    if r.is_success:
        console.print(f"[green]✓[/] Agent deleted")
    else:
        console.print(f"[red]✗[/] {r.json().get('detail', r.text)}")


@agent.command("restart")
@click.argument("agent_id")
def agent_restart(agent_id):
    """Stop then start a Pi agent."""
    import httpx

    base = os.getenv("CHIBU_CONTROL_URL", "http://localhost:8000")
    httpx.post(f"{base}/agents/{agent_id}/stop")
    r = httpx.post(f"{base}/agents/{agent_id}/start")
    if r.is_success:
        d = r.json()
        console.print(f"[green]✓[/] Agent restarted (pid={d.get('pid')})")
    else:
        console.print(f"[red]✗[/] {r.json().get('detail', r.text)}")


@agent.command("logs")
@click.argument("agent_id")
@click.option("--lines", "-n", default=50, help="Number of tail lines to show")
def agent_logs(agent_id, lines):
    """Print the last N lines of an agent's log file."""
    import httpx

    base = os.getenv("CHIBU_CONTROL_URL", "http://localhost:8000")
    try:
        a = httpx.get(f"{base}/agents/").json()
        rec = next((x for x in a if x["agent_id"] == agent_id), None)
    except Exception as e:
        console.print(f"[red]Error:[/] {e}")
        return

    if rec is None:
        console.print(f"[red]Agent {agent_id!r} not found[/]")
        return

    # workspace_path not exposed in list — look it up via status endpoint
    workspace = Path(os.getenv("CHIBU_AGENTS_DIR", "agents"))
    chiboo, name = agent_id.rsplit("_", 1) if "_" in agent_id else ("", agent_id)
    log_path = workspace / chiboo / name / "agent.log"
    if not log_path.exists():
        # Try flat layout
        log_path = workspace / agent_id / "agent.log"

    if not log_path.exists():
        console.print(f"[yellow]Log not found:[/] {log_path}")
        return

    log_lines = log_path.read_text(errors="replace").splitlines()
    for line in log_lines[-lines:]:
        console.print(line)


@agent.command("connect")
@click.option("--host", default="localhost")
@click.option("--port", required=True, type=int)
@click.option("--token", required=True, help="40-char auth token")
@click.argument("prompt")
def agent_connect(host, port, token, prompt):
    """Execute a prompt on a running Pi agent via gRPC."""

    async def _run():
        from chibu.grpc_server.client import ChibuClient

        async with ChibuClient(host, port, token) as client:
            if not await client.ping():
                console.print("[red]Agent not reachable[/]")
                return
            console.print(f"[violet]χ[/] Executing on {host}:{port} …\n")
            async for event in client.execute(prompt):
                if event.event_type == "text":
                    console.print(event.content, end="")
                elif event.event_type == "tool_use":
                    console.print(f"\n[dim][tool: {event.tool_name}][/]")
                elif event.event_type == "error":
                    console.print(f"\n[red]Error:[/] {event.content}")
            console.print()

    asyncio.run(_run())


# ── group ─────────────────────────────────────────────────────────────────────


@main.group()
def group():
    """Manage chiboos (agent groups)."""


@group.command("list")
def group_list():
    """List all chiboos."""
    import httpx

    base = os.getenv("CHIBU_CONTROL_URL", "http://localhost:8000")
    try:
        chiboos = httpx.get(f"{base}/chiboos/").json()
    except Exception as e:
        console.print(f"[red]Error:[/] {e}")
        return

    t = Table(title="Chiboos", show_lines=True)
    for col in ("Name", "Agents", "Description"):
        t.add_column(col)
    for c in chiboos:
        t.add_row(c["name"], str(c["agent_count"]), c.get("description", ""))
    console.print(t)


@group.command("create")
@click.argument("name")
@click.option("--description", "-d", default="")
def group_create(name, description):
    """Create a new chiboo."""
    import httpx

    base = os.getenv("CHIBU_CONTROL_URL", "http://localhost:8000")
    r = httpx.post(f"{base}/chiboos/", json={"name": name, "description": description})
    if r.is_success:
        console.print(f"[green]✓[/] Chiboo '{name}' created")
    else:
        console.print(f"[red]✗[/] {r.json().get('detail', r.text)}")


# ── health ────────────────────────────────────────────────────────────────────


@main.command()
def health():
    """Check control plane health."""
    import httpx

    base = os.getenv("CHIBU_CONTROL_URL", "http://localhost:8000")
    try:
        r = httpx.get(f"{base}/health")
        d = r.json()
        if d.get("status") == "ok":
            console.print(f"[green]✓[/] healthy  db={d.get('db')}")
        else:
            console.print(f"[yellow]⚠[/] degraded  db={d.get('db')}")
    except Exception as e:
        console.print(f"[red]✗[/] unreachable: {e}")
