# Chibu — Pi Agent Platform

Chibu is a multi-agent orchestration platform built by badmono org. It manages a fleet of **pi agents** — persistent AI coding assistants powered by [badlogic/pi-mono](https://github.com/badlogic/pi-mono) — behind a gRPC interface, a REST control plane, and a live web dashboard.

Each agent runs as a `pi --mode rpc` subprocess. Chibu wraps it with a gRPC server (one per agent), a shared control plane (FastAPI), and a SQLAlchemy-backed registry. Agents are grouped into **chiboos** (agent groups). Skills and extensions live in the agent's `.pi/` workspace; mutations hot-reload the subprocess without closing the gRPC port.

---

## Table of Contents

- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running](#running)
- [Concepts](#concepts)
- [API Reference](#api-reference)
- [gRPC Interface](#grpc-interface)
- [CLI](#cli)
- [OpenTelemetry](#opentelemetry)
- [Testing](#testing)
- [Environment Variables](#environment-variables)

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    Browser / API Client                       │
│          (dashboard UI, REST calls, gRPC clients)            │
└────────────────┬──────────────────────────┬──────────────────┘
                 │ HTTP / SSE               │ gRPC (per agent)
                 ▼                          ▼
┌───────────────────────┐      ┌────────────────────────────────┐
│   Control Plane       │      │   Agent gRPC Server            │
│   (FastAPI, port 8000)│      │   (one process per agent,      │
│                       │      │    port 50051–50200)           │
│  • Agent CRUD         │      │                                │
│  • Skill/ext editing  │      │  ChiAgentServicer              │
│  • SSE execute stream │      │  • Execute → stream events     │
│  • Live log tailing   │      │  • Reload → restart pi         │
│  • Analytics          │      │  • GetInfo, ListSkills, Ping   │
└───────┬───────────────┘      └─────────────┬──────────────────┘
        │ DB (SQLAlchemy)                     │ asyncio subprocess
        │ spawn subprocess                    ▼
        ▼                        ┌─────────────────────────┐
┌───────────────────┐            │   PiAgent               │
│   SQLite / PG     │            │   pi --mode rpc         │
│                   │            │   (JSON-RPC stdin/out)  │
│  AgentGroup       │            │                         │
│  Agent            │            │  .pi/                   │
│  AgentEvent       │            │    skills/   (SKILL.md) │
│  PerformanceMetric│            │    extensions/ (.ts)    │
└───────────────────┘            │    packages/            │
                                 │  AGENTS.md              │
                                 └─────────────────────────┘
```

**Data flow for a prompt:**

1. Client sends `POST /ws/{agent_id}/execute` (SSE) or gRPC `Execute` RPC.
2. Control plane / gRPC servicer opens a `ChibuClient` connection to the agent's gRPC port.
3. `ChiAgentServicer.Execute` calls `PiAgent.execute()`, which writes `set_model` + `prompt` commands to the pi subprocess stdin.
4. Pi emits JSON events on stdout; `PiAgent` yields them as `AgentEvent` objects.
5. The servicer streams `ExecuteResponse` messages back over gRPC.
6. The SSE endpoint forwards each event as `data: {json}\n\n` to the browser.

---

## Prerequisites

- **Python 3.11+**
- **pi CLI** — `npm install -g @badlogic/pi` — must be on `$PATH` as `pi`
- **ANTHROPIC_API_KEY** — set in the shell (pi reads it directly)
- SQLite (bundled) or PostgreSQL for production

> The pi executable is the only LLM caller. Chibu contains no Anthropic SDK dependency.

---

## Installation

```bash
git clone https://github.com/chandan84/skilledpi
cd skilledpi
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

---

## Configuration

### `defaults/config.yaml`

Loaded by the agent gRPC server subprocess at startup. Key sections:

```yaml
agent:
  version: "0.1.0"
  org: "badmono"

otel:
  enabled: false
  endpoint: "http://localhost:4317"    # OTLP gRPC
  http_endpoint: "http://localhost:4318"
  service_name: "chibu-pi-agent"
  protocol: "grpc"   # grpc | http

logging:
  level: "INFO"
```

Override any value with environment variables (see [Environment Variables](#environment-variables)).

### `defaults/models.json`

Defines the Anthropic provider and two named models for pi:

| Alias   | Model ID              | Reasoning | Notes                        |
|---------|----------------------|-----------|------------------------------|
| `staah` | `claude-opus-4-7`    | yes       | Heavy thinker, low verbose   |
| `faah`  | `claude-sonnet-4-6`  | no        | Light thinker, medium verbose|

Requests that omit `model` or supply an unrecognized name default to **faah**.

---

## Running

### Control plane

```bash
# Development (auto-reload on file changes)
python main.py --reload

# Production (single worker — required; see Architecture notes)
python main.py --host 0.0.0.0 --port 8000

# Via CLI
chibu serve
```

Open `http://localhost:8000` for the dashboard.

### Individual agent gRPC server

The control plane launches agent subprocesses automatically when you click **Start** in the UI or call `POST /agents/{id}/start`. Each subprocess runs:

```bash
python -m chibu.grpc_server.server <agent_id> <registry_snapshot_path>
```

You do not normally invoke this directly.

### Regenerate gRPC bindings

```bash
chibu proto
# or
python -m grpc_tools.protoc -I proto --python_out=chibu/grpc_server \
    --grpc_python_out=chibu/grpc_server proto/chibu_agent.proto
```

---

## Concepts

### Chiboos

A **chiboo** is a named agent group. Every agent belongs to exactly one chiboo. Chiboo names appear in the composite agent ID.

```
chiboo name:  research
agent name:   coder-1
agent ID:     research_coder-1
```

Slugification rules: lowercase, alphanumeric + hyphens, runs of non-alnum collapsed to `-`, leading/trailing hyphens stripped.

### Agent Workspace

Each agent gets a dedicated workspace directory (default: `agents/{agent_id}/`). At bootstrap, `filesystem.bootstrap_agent_root()` creates:

```
agents/research_coder-1/
├── AGENTS.md              # System context injected by pi into every prompt
└── .pi/
    ├── agent.json         # Identity: agent_id, name, chiboo
    ├── skills/            # SKILL.md files (pi discovers these automatically)
    ├── extensions/        # .ts extension files
    └── packages/          # Node packages consumed by extensions
```

### Skills

Skills are directories inside `.pi/skills/` that contain a `SKILL.md` file. Pi discovers and exposes them as tools automatically.

Manage via the control plane:
- `GET  /agents/{id}/skills` — list installed skills
- `POST /agents/{id}/skills` — add a skill (name + markdown content)
- `DELETE /agents/{id}/skills/{name}` — remove a skill

Every write triggers a hot-reload (pi subprocess restart).

### Extensions

Extensions are TypeScript files in `.pi/extensions/`. They hook into pi's lifecycle (before/after each action, on context compaction, etc.).

Manage via:
- `GET  /agents/{id}/extensions`
- `POST /agents/{id}/extensions` — name + TypeScript source
- `DELETE /agents/{id}/extensions/{name}`

Every write triggers a hot-reload.

### Hot Reload

When skills or extensions change, the control plane:
1. Writes the new file to `.pi/skills/` or `.pi/extensions/`.
2. Calls `ChibuClient.reload()` → gRPC `Reload` RPC → `PiAgent.restart()`.
3. `restart()` stops the pi subprocess and starts a new one in the same workspace.
4. The gRPC port stays open throughout; the reload is invisible to clients mid-request (in-flight executions drain first).

### Agent Lifecycle

```
stopped → starting → running → stopped
              ↓
            error
```

- `stopped` — no pi subprocess running
- `starting` — gRPC server subprocess spawned; waiting for ping readiness (30s timeout)
- `running` — ping confirmed, accepting Execute RPCs
- `error` — subprocess exited unexpectedly

### Models

Pass the model alias per-request:

```json
{ "prompt": "refactor this", "model": "staah" }
```

- `staah` — Claude Opus 4.7 with extended reasoning (slower, highest quality)
- `faah` — Claude Sonnet 4.6 (faster, default for all requests)

---

## API Reference

Base URL: `http://localhost:8000`

### Dashboard

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard HTML |
| `GET` | `/api/summary` | Agent + chiboo counts |
| `GET` | `/api/analytics` | Status distribution + agents-per-chiboo |

### Chiboos

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/chiboos/` | List all chiboos |
| `POST` | `/chiboos/` | Create a chiboo `{"name": str, "description": str}` |
| `DELETE` | `/chiboos/{name}` | Delete empty chiboo (409 if agents exist) |

### Agents

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/agents/` | List all agents |
| `POST` | `/agents/` | Create agent `{"name": str, "chiboo": str}` |
| `GET` | `/agents/{id}` | Agent detail HTML |
| `DELETE` | `/agents/{id}` | Delete stopped agent (409 if running) |
| `POST` | `/agents/{id}/start` | Start gRPC server subprocess |
| `POST` | `/agents/{id}/stop` | Stop gRPC server subprocess |
| `GET` | `/agents/{id}/status` | Current status + pid |

### Skills & Extensions

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/agents/{id}/skills` | List skills |
| `POST` | `/agents/{id}/skills` | Add skill `{"name": str, "content": str}` |
| `DELETE` | `/agents/{id}/skills/{name}` | Remove skill |
| `GET` | `/agents/{id}/extensions` | List extensions |
| `POST` | `/agents/{id}/extensions` | Add extension `{"name": str, "source": str}` |
| `DELETE` | `/agents/{id}/extensions/{name}` | Remove extension |

### Streaming

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/ws/{id}/execute` | SSE execute stream |
| `WS` | `/ws/{id}/logs` | WebSocket live log tail |

**SSE execute body:**
```json
{
  "prompt": "string",
  "model": "faah",
  "session_id": "optional-uuid",
  "new_session": false,
  "compact_first": false,
  "timeout_seconds": 120
}
```

**SSE event format:**
```
data: {"event_type": "text", "content": "hello", "session_id": "...", "timestamp": 1234567890}

data: {"event_type": "done", "is_done": true, ...}
```

Event types: `agent_start`, `text`, `tool_use`, `tool_update`, `tool_result`, `done`, `error`

---

## gRPC Interface

Proto: `proto/chibu_agent.proto`  
Generated bindings: `chibu/grpc_server/chibu_agent_pb2*.py`

### Service: `ChiAgent`

All RPCs require `auth_token` matching the agent's token from the registry.

#### `Execute(ExecuteRequest) → stream ExecuteResponse`

Stream prompt execution events.

```protobuf
message ExecuteRequest {
  string auth_token     = 1;
  string prompt         = 2;
  string session_id     = 3;
  string model          = 4;   // "staah" | "faah"; defaults to "faah"
  bool   new_session    = 5;
  bool   compact_first  = 6;
  repeated string files = 7;
  int32  timeout_seconds = 8;
}
```

#### `Reload(ReloadRequest) → ReloadResponse`

Restart the pi subprocess (used after skill/extension mutations).

#### `GetInfo(InfoRequest) → InfoResponse`

Returns agent metadata including `agent_id`, `name`, `chiboo`, `status`, `grpc_port`, available skills and models.

#### `ListSkills(ListSkillsRequest) → ListSkillsResponse`

Returns installed skills with name, description, version, and parameters.

#### `Ping(PingRequest) → PongResponse`

Health check; returns echoed message and server timestamp.

### Using `ChibuClient`

```python
from chibu.grpc_server.client import ChibuClient

async with ChibuClient("localhost", 50051) as client:
    # Health check
    pong = await client.ping()

    # Stream a prompt
    async for event in client.execute(
        auth_token="<token>",
        prompt="explain this code",
        model="faah",
    ):
        print(event.event_type, event.content)

    # Hot-reload after workspace changes
    await client.reload(auth_token="<token>")
```

---

## CLI

```bash
chibu --help

# Start the control plane
chibu serve [--host 0.0.0.0] [--port 8000] [--reload]

# Regenerate gRPC Python bindings
chibu proto

# Agent operations (requires running control plane)
chibu agent list
chibu agent start <agent-id>
chibu agent connect <agent-id> --prompt "write a sorting algorithm"
```

---

## OpenTelemetry

OTEL is disabled by default. Enable it by setting `CHIBU_OTEL_ENABLED=true`.

### Traces

Each `Execute` RPC creates a span `chibu.agent.execute` with attributes:

| Attribute | Value |
|-----------|-------|
| `agent.id` | Composite agent ID |
| `agent.chiboo` | Chiboo name |
| `agent.model` | Model alias used |

### Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `chibu.prompt.count` | Counter | Total prompts executed |
| `chibu.prompt.duration_ms` | Histogram | End-to-end execution time |
| `chibu.agent.active` | UpDownCounter | Currently-executing agents |
| `chibu.tool_call.count` | Counter | Total tool calls across all agents |
| `chibu.agent.error.count` | Counter | Failed executions |

### Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `CHIBU_OTEL_ENABLED` | `false` | Enable OTEL export |
| `CHIBU_OTEL_ENDPOINT` | `http://localhost:4317` | OTLP gRPC collector |
| `CHIBU_OTEL_HTTP_ENDPOINT` | `http://localhost:4318` | OTLP HTTP collector |
| `CHIBU_OTEL_PROTOCOL` | `grpc` | `grpc` or `http` |
| `CHIBU_OTEL_SERVICE` | `chibu-pi-agent` | Resource service name |

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific suite
pytest tests/test_registry.py -v
pytest tests/test_control_plane_api.py -v
```

Tests use an in-memory SQLite database (via `StaticPool`) and override FastAPI dependencies. No real pi subprocess or network calls are made.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | **Required.** Passed to pi subprocess |
| `CHIBU_DB_URL` | `sqlite+aiosqlite:///./chibu.db` | SQLAlchemy database URL |
| `CHIBU_AGENTS_DIR` | `agents` | Root directory for agent workspaces |
| `CHIBU_REGISTRY_SNAPSHOT` | `chibu_registry.json` | JSON snapshot path for subprocess bootstrap |
| `CHIBU_CONTROL_HOST` | `0.0.0.0` | Control plane bind host |
| `CHIBU_CONTROL_PORT` | `8000` | Control plane bind port |
| `CHIBU_LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `CHIBU_OTEL_ENABLED` | `false` | Enable OpenTelemetry export |
| `CHIBU_OTEL_ENDPOINT` | `http://localhost:4317` | OTLP gRPC endpoint |
| `CHIBU_OTEL_HTTP_ENDPOINT` | `http://localhost:4318` | OTLP HTTP endpoint |
| `CHIBU_OTEL_PROTOCOL` | `grpc` | OTLP protocol |
| `CHIBU_OTEL_SERVICE` | `chibu-pi-agent` | OTel service name |

---

## Project Layout

```
skilledpi/
├── chibu/                          # Main Python package
│   ├── agent/
│   │   └── pi_agent.py             # PiAgent: pi subprocess lifecycle + execution
│   ├── cli.py                      # Click CLI entry point
│   ├── control_plane/
│   │   ├── app.py                  # FastAPI factory + lifespan
│   │   ├── deps.py                 # Shared FastAPI dependencies
│   │   ├── routers/
│   │   │   ├── agents.py           # Agent CRUD, lifecycle, skills, extensions
│   │   │   ├── chiboos.py          # Chiboo (group) CRUD
│   │   │   ├── dashboard.py        # Dashboard HTML + analytics API
│   │   │   └── ws.py               # SSE execute stream + WebSocket log tail
│   │   └── templates/
│   │       ├── index.html          # Dashboard (Claude design language)
│   │       └── agent_detail.html   # Agent detail with chat panel
│   ├── db/
│   │   ├── engine.py               # Async SQLAlchemy engine + session
│   │   └── models.py               # ORM models
│   ├── grpc_server/
│   │   ├── chibu_agent_pb2*.py     # Generated protobuf / gRPC bindings
│   │   ├── client.py               # ChibuClient (async gRPC client)
│   │   ├── server.py               # gRPC server entry point (per-agent subprocess)
│   │   └── servicer.py             # ChiAgentServicer implementation
│   ├── otel/
│   │   ├── metrics.py              # Metric instruments + helper functions
│   │   └── tracing.py              # Span initialization + record_execute_span
│   ├── process/
│   │   └── manager.py              # AgentProcessManager (spawn/stop/readiness)
│   ├── registry/
│   │   └── agent_registry.py       # AgentRegistry (DB queries for agents + groups)
│   └── utils/
│       ├── auth.py                 # Token generation + validation
│       └── filesystem.py           # Workspace bootstrapper
├── defaults/
│   ├── config.yaml                 # Default agent configuration
│   └── models.json                 # pi model provider definition
├── proto/
│   └── chibu_agent.proto           # gRPC service definition
├── tests/
│   ├── test_auth.py
│   ├── test_control_plane_api.py
│   └── test_registry.py
├── main.py                         # Control plane uvicorn entry point
├── requirements.txt
└── pyproject.toml
```
