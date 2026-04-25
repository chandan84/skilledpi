# Chibu — Architecture & Agent Guidelines

This file is the authoritative reference for contributors and AI coding agents working on this codebase. It covers package structure, module boundaries, architectural invariants, and patterns to follow or avoid.

---

## Package Structure

```
chibu/
├── agent/          pi subprocess wrapper (PiAgent)
├── cli.py          Click CLI entry point
├── control_plane/  FastAPI web server (REST API + dashboard)
│   ├── routers/    One file per resource domain
│   └── templates/  Jinja2 HTML templates
├── db/             SQLAlchemy models + async engine
├── grpc_server/    gRPC server, servicer, and client
├── otel/           OpenTelemetry tracing + metrics (optional)
├── process/        OS-level subprocess management
├── registry/       High-level DB queries for agent registry
└── utils/          Shared utilities (auth, filesystem)
```

### Module Ownership

| Layer | Owns | Must not |
|-------|------|----------|
| `agent/` | pi subprocess protocol, event parsing, skill introspection | Import from `control_plane`, `process`, `registry` |
| `grpc_server/servicer.py` | gRPC RPC implementations | Import from `control_plane` |
| `grpc_server/server.py` | gRPC server lifecycle | Import from `control_plane` |
| `control_plane/` | HTTP endpoints, SSE, HTML | Spawn processes directly — delegate to `process.manager` |
| `registry/` | All DB queries for agents and groups | Import from `control_plane`, `process`, `grpc_server` |
| `process/` | OS subprocess spawn/stop/readiness | Import from `control_plane`, `registry` |
| `otel/` | OTEL provider init, span helpers, metric helpers | Import from any other chibu module |
| `utils/` | Stateless helpers (auth, filesystem) | Import from any other chibu module |

The dependency graph flows downward:

```
control_plane
    ↓
process  ←→  registry  ←→  db
    ↓
grpc_server/client
    ↓
grpc_server/servicer
    ↓
agent
    ↓
utils / otel
```

Cycles between any of these layers are a hard error.

---

## Architectural Invariants

### 1. No Anthropic SDK

The Anthropic SDK (`anthropic` Python package) must not appear anywhere in this codebase. All LLM calls are made by the `pi` CLI subprocess, not by Python code.

```python
# WRONG
from anthropic import AsyncAnthropic
client = AsyncAnthropic()

# RIGHT — pi does this internally
await agent.execute(prompt="...", model="faah")
```

### 2. gRPC is the External Interface

Every agent exposes a gRPC server. The control plane communicates with agents through `ChibuClient`, not by calling `PiAgent` directly. External clients (users, other services) also use gRPC.

```python
# WRONG — control plane importing PiAgent
from chibu.agent.pi_agent import PiAgent
agent = PiAgent(...)
async for event in agent.execute(...): ...

# RIGHT — control plane using ChibuClient
from chibu.grpc_server.client import ChibuClient
async with ChibuClient(host, port) as client:
    async for event in client.execute(...): ...
```

The sole exception: `grpc_server/servicer.py` owns a `PiAgent` instance and calls it directly. That is the single crossing point between the two protocols.

### 3. Composite Agent IDs Are Strings

Agent IDs are human-readable composite strings (`{chiboo_slug}_{agent_slug}`), not UUIDs. They are the primary key of the `Agent` table and are used in gRPC auth, file paths, and API routes. Never generate a UUID for an agent.

```python
# WRONG
import uuid; agent_id = str(uuid.uuid4())

# RIGHT
agent_id = f"{_slug(chiboo_name)}_{_slug(agent_name)}"
```

Slugification: lowercase, alphanumeric + hyphens, non-alnum runs collapsed to `-`, leading/trailing hyphens stripped.

### 4. Hot-Reload Never Closes the gRPC Port

When skills or extensions are mutated, only the pi subprocess restarts. The gRPC server process stays alive. Use the `Reload` RPC:

```
skill mutation → write .pi/skills/<name>/ → ChibuClient.reload() → Reload RPC → PiAgent.restart()
```

Never stop and restart the gRPC server process in response to a skill/extension change.

### 5. Single Control Plane Worker

`AgentProcessManager` stores subprocess handles in memory. The control plane **must** run with a single uvicorn worker (`--workers 1`). Multi-worker deployments would produce split-brain process tracking (worker A started an agent, worker B receives a stop request and has no handle). The `main.py` entry point enforces this.

### 6. Atomic Registry Snapshot Writes

The registry snapshot (`chibu_registry.json`) is read by agent subprocesses at startup. All writes must be atomic:

```python
# WRONG — partial write visible to readers
path.write_text(json.dumps(data))

# RIGHT — atomic rename
tmp = path.with_suffix(".tmp")
tmp.write_text(json.dumps(data))
tmp.replace(path)  # POSIX atomic
```

### 7. SSE for Browser Streaming, gRPC for Everything Else

The browser uses the SSE endpoint (`POST /ws/{id}/execute`) because browsers cannot directly consume gRPC streams. All server-side and CLI consumers use gRPC. Do not add WebSocket streaming for execution; SSE is sufficient and simpler.

---

## Key Patterns

### PiAgent Event Loop

`PiAgent.execute()` is an async generator. It writes two JSON commands to pi's stdin, then reads newline-delimited JSON events from stdout until it receives an `agent_end` event or the timeout fires.

```python
# Command sequence
{"command": "set_model", "model": "faah"}
{"command": "prompt", "text": "...", "sessionId": "..."}

# Pi emits events until agent_end
{"type": "agent_start", ...}
{"type": "message_update", ...}
{"type": "tool_execution_start", ...}
{"type": "agent_end", ...}
```

`_map_pi_event()` normalises camelCase pi event types to snake_case `AgentEvent` objects. Add new pi event types here, not in the servicer.

### gRPC Auth Pattern

Every RPC validates the auth token before doing anything else:

```python
if not agent.verify_token(request.auth_token):
    await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid auth token")
    return
```

After `abort()`, return immediately. Do not call `return` before `abort()` — the gRPC framework must flush the status code first.

### Dependency Injection in FastAPI

All shared state is provided via FastAPI `Depends`:

- `get_session` → yields `AsyncSession` (one per request, auto-commit/rollback)
- `get_registry` → yields `AgentRegistry` wrapping the session
- `get_process_manager` → returns singleton `AgentProcessManager` (lru_cache)

Never instantiate `AgentRegistry` or `AgentProcessManager` inside a route function body. Always inject them.

### Registry Group-or-Create

Creating an agent automatically creates its chiboo if it doesn't exist. `AgentRegistry.get_or_create_group()` handles this idempotently. Routers do not need to pre-create chiboos.

### OTEL — No-Op by Default

All OTEL functions are safe to call unconditionally. When `CHIBU_OTEL_ENABLED` is not set, every call is a no-op:

```python
# Always safe — no-ops if OTEL disabled
with record_execute_span(agent_id, chiboo, model):
    ...
record_prompt(agent_id, duration_ms=150)
```

Do not guard OTEL calls with `if otel_enabled` checks in business logic.

### Workspace Layout

The canonical workspace structure is created once by `bootstrap_agent_root()` and never restructured. Code that reads from `.pi/skills/` or `.pi/extensions/` must tolerate the directory not existing (agent might not have been bootstrapped yet).

```python
skills_dir = workspace / ".pi" / "skills"
if not skills_dir.exists():
    return []
```

---

## Anti-Patterns

### Do Not Import Between Sibling Routers

FastAPI routers are sibling modules. They must not import each other. Shared logic goes in `deps.py` or a utility module.

```python
# WRONG — agents.py importing from chiboos.py
from chibu.control_plane.routers.chiboos import _chiboo_dict

# RIGHT — shared logic in registry or a dedicated helper
group = await registry.get_group_by_name(name)
```

### Do Not Block the Event Loop

All I/O in async paths must be awaited. File reads in route handlers and servicer methods must use `aiofiles`. The only exception: tiny synchronous reads that complete in microseconds (e.g., reading a small JSON file once at startup).

```python
# WRONG — blocking file read in async handler
content = open(path).read()

# RIGHT
import aiofiles
async with aiofiles.open(path) as f:
    content = await f.read()
```

### Do Not Hardcode Model IDs

Model IDs (`claude-opus-4-7`, `claude-sonnet-4-6`) must not appear in Python source. Use model aliases (`staah`, `faah`) in all application code. The mapping lives exclusively in `defaults/models.json` and is consumed by the `pi` process.

```python
# WRONG
model = "claude-opus-4-7"

# RIGHT
model = request.model or "faah"
```

### Do Not Update Agent Status Before Readiness

Setting an agent's DB status to `running` before the gRPC ping succeeds is incorrect. The status must reflect confirmed readiness, not the intent to start.

```python
# WRONG — status set immediately after subprocess spawn
pm.start(agent_id)
await registry.update_status(agent_id, "running")

# RIGHT — status set only after readiness confirmed
pid = await pm.start(agent_id)  # start() awaits _wait_ready() internally
await registry.update_status(agent_id, "running", pid=pid)
```

### Do Not Use strftime() in Queries

SQLite's `func.strftime()` does not work on PostgreSQL. Use SQLAlchemy-portable expressions or perform time bucketing client-side.

```python
# WRONG — SQLite only
func.strftime("%Y-%m-%dT%H:00:00", LLMRequest.created_at)

# RIGHT — works on both
# Group by hour client-side after fetching raw timestamps
```

### Do Not Leak File Handles

Log file handles opened for agent subprocesses must be tracked and closed on stop:

```python
# WRONG — handle never closed
log_fh = open(log_path, "a")
subprocess.Popen(..., stdout=log_fh)

# RIGHT — track and close in stop()
self._log_fhs[agent_id] = open(log_path, "a")
# ... in stop():
self._log_fhs.pop(agent_id).close()
```

### Do Not Add Dead Accumulator Variables

Variables that accumulate state during streaming but are never read at the end are dead code. Remove them rather than leaving them in place.

```python
# WRONG — tool_calls is accumulated but never used
tool_calls = []
async for event in agent.execute(...):
    if event.event_type == "tool_use":
        tool_calls.append(event)  # dead

# RIGHT — remove the accumulator entirely
async for event in agent.execute(...):
    yield _to_response(event)
```

### Do Not Use String Matching for Code Analysis

When analysing code submitted to skills (e.g., run_python), use AST analysis rather than string matching. String matching is trivially bypassed.

```python
# WRONG
if "import os" in code:
    raise ValueError("Forbidden")

# RIGHT
import ast
tree = ast.parse(code)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        raise ValueError("Imports not allowed")
```

---

## Database Models

### Agent

| Column | Type | Notes |
|--------|------|-------|
| `agent_id` | `String(240)` | PK, composite slug: `{chiboo}_{name}` |
| `name` | `String` | Display name |
| `group_id` | `Integer` | FK → AgentGroup.id |
| `auth_token` | `String(40)` | Alphanumeric, cryptographically random |
| `grpc_port` | `Integer` | Unique, 50051–50200 |
| `workspace_path` | `String` | Absolute path to agent workspace |
| `status` | `String` | `stopped | starting | running | error` |
| `pid` | `Integer` | Nullable; OS PID of gRPC server subprocess |
| `created_at` | `DateTime` | UTC |
| `updated_at` | `DateTime` | UTC, auto-updated |

Unique constraints: `(name, group_id)`, `grpc_port`.

### AgentGroup (Chiboo)

| Column | Type | Notes |
|--------|------|-------|
| `id` | `Integer` | PK |
| `name` | `String` | Unique, human-readable slug |
| `description` | `String` | Optional |
| `created_at` | `DateTime` | UTC |

### AgentEvent

Append-only audit log of agent lifecycle events (start, stop, reload, error).

### PerformanceMetric

Numeric metrics emitted by extensions or instrumentation. Use OTEL for time-series telemetry; use this table for durable per-agent metrics that need to survive OTEL collector restarts.

---

## gRPC Proto Conventions

- All RPCs require `auth_token` as the first field.
- Use `string` for timestamps over the wire (ISO 8601); convert to `int64` Unix milliseconds only for `timestamp` fields already defined that way.
- Streaming RPCs: client-streaming and bidirectional streaming are not used. Only server-streaming (`Execute`).
- After adding a new RPC to the `.proto`, always regenerate bindings with `chibu proto` and commit both `chibu_agent_pb2.py` and `chibu_agent_pb2_grpc.py`.
- Field numbers are permanent. Never reuse a retired field number.

---

## Testing Conventions

- Tests use `sqlite+aiosqlite:///:memory:` with `StaticPool` so `create_all` and the session share the same connection.
- Each test gets a fresh DB via the `session` or `_test_db` fixture — no shared state between tests.
- FastAPI dependency overrides replace `get_session` with the test session factory. Tests must call `deps.get_process_manager.cache_clear()` before and after to avoid leaking the singleton.
- No real pi subprocess is spawned in tests. Process manager behavior is tested at the control plane API level by directly manipulating DB status via the registry.
- Test file naming: `test_{module}.py`. One file per module under test.
- All async tests use `@pytest.mark.asyncio`. Configure `asyncio_mode = "auto"` in `pytest.ini` to avoid decorator boilerplate on fixtures.

---

## Adding a New Capability

### New gRPC RPC

1. Add message and RPC definition to `proto/chibu_agent.proto`.
2. Run `chibu proto` to regenerate bindings.
3. Implement the method in `chibu/grpc_server/servicer.py`.
4. Add the corresponding method to `ChibuClient` in `chibu/grpc_server/client.py`.
5. If the RPC requires new `PiAgent` functionality, add it to `chibu/agent/pi_agent.py` first.

### New REST Endpoint

1. Add the route to the appropriate router in `chibu/control_plane/routers/`.
2. Inject `AgentRegistry` and/or `AgentProcessManager` via `Depends`.
3. Do not add business logic to the route function — delegate to registry methods or helper functions in the same router file.
4. If the new route needs to communicate with a running agent, use `ChibuClient`, not `PiAgent` directly.

### New DB Column

1. Add the column to the ORM model in `chibu/db/models.py`.
2. Add `index=True` if the column will appear in a `WHERE` or `ORDER BY` clause.
3. Update `AgentRegistry` methods that return dicts to include the new field.
4. If Alembic is set up, generate a migration: `alembic revision --autogenerate -m "add <column>"`.
5. If using `create_all` (development), drop and recreate the DB or add the column manually.

### New OTEL Metric

1. Add the instrument to `chibu/otel/metrics.py` alongside the existing counters/histograms.
2. Add a `record_<thing>()` helper function in the same file.
3. Export the helper from `chibu/otel/__init__.py`.
4. Call it from the appropriate location (servicer for per-RPC metrics, PiAgent for per-execution metrics).
5. Verify the metric is a no-op when OTEL is disabled (the SDK guarantees this for all standard instruments).
