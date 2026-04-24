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
        console.print(f"[green]✓[/] Agent started (pid={d.get('pid')})")
    else:
        console.print(f"[red]✗[/] {r.json().get('detail', r.text)}")


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
