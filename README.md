# Gastown 🏙️

**Gastown** is a Python 3.11+ multi-agent engineering coordinator built on FastAPI. You describe a goal in plain English; Gastown decomposes it into atomic work items (_beads_), dispatches parallel worker agents (_PoleCATs_) that read and write code in isolated Git worktrees, monitors their health with a watchdog (_Witness_), and merges completed branches via a bors-style bisecting queue (_Refinery_). A coordinating _Mayor_ agent ties everything together without ever writing a line of code itself.

---

## Contents

- [What is Gastown](#what-is-gastown)
- [Agent roles](#agent-roles)
- [Beads and Convoys](#beads-and-convoys)
- [Quick Start](#quick-start)
- [Architecture Overview](#architecture-overview)
- [Configuration](#configuration)
- [Usage Examples](#usage-examples)
- [PoleCAT Tool Reference](#polecat-tool-reference)
- [Deployment](#deployment)
- [Testing](#testing)
- [Performance](#performance)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [Planned Features / Known Limits](#planned-features--known-limits)

---

## What is Gastown

**Problem:** Running an LLM on a large engineering task from start to finish is slow, unreliable, and hard to parallelize. A single context window easily overflows; a single failure derails everything.

**Solution:** Gastown decomposes goals into small, independently solvable tasks. Each task runs in its own Git worktree with its own LLM agent loop. Progress is monitored in real time. Completed branches are merged in priority order with automatic conflict resolution.

```
You: "Add rate-limiting to the API and write tests for it."

Gastown →
  Mayor decomposes → bead: implement-rate-limit  (priority 0)
                   → bead: write-rate-limit-tests (priority 1)
  PoleCAT-1 works on bead 1 (isolated branch)
  PoleCAT-2 works on bead 2 (isolated branch)
  Refinery merges bead 1 → bead 2 → main
  Mayor reviews
```

---

## Agent Roles

| Agent | Role | LLM? |
|-------|------|-------|
| **Mayor** | Decomposes goals into beads, dispatches convoys, reviews results | ✅ |
| **PoleCAT** | Ephemeral worker; runs a tool-use loop to implement exactly one bead | ✅ |
| **Witness** | Watchdog; monitors heartbeats, nudges stuck agents, cancels hopeless ones | ❌ |
| **Refinery** | Bors-style bisecting merge queue; merges bead branches into `main`/`master` | ❌ |

### Mayor

The Mayor is a senior coordinator. It never writes code. It:

1. Fetches the repository file tree.
2. Makes a single structured LLM call to produce a `DecompositionResult` (list of `BeadSpec` objects).
3. Persists each bead to SQLite.
4. Dispatches all beads as a convoy.
5. Reviews completed work at the end and produces a short summary.

### PoleCAT

Each PoleCAT lives for exactly one bead. It runs an agentic loop:

```
while tool_calls < MAX_TOOL_CALLS (30):
    LLM call with current message history
    execute tool calls (read_file / write_file / list_directory / run_command)
    if done_signal called → commit changes, mark bead DONE, exit
```

Heartbeat events are emitted after each LLM round so the Witness can detect stalling.

### Witness

The Witness runs concurrently with all PoleCATs. It:

- Tracks the last heartbeat time per bead.
- After `GASTOWN_STUCK_TIMEOUT_SECONDS` (default 120 s) of silence, sends a nudge message into the PoleCAT's queue.
- After `MAX_NUDGES` (3) nudges without progress, cancels the task and marks the bead `FAILED`.

### Refinery

The Refinery processes completed bead branches after all PoleCATs finish:

1. Sort beads by priority.
2. Try to merge all as a batch (`git merge --no-ff`).
3. On conflict: bisect → retry first half, re-queue second half.
4. A single-bead batch that still conflicts is rejected.
5. Roll back any partially-merged beads on failure.

---

## Beads and Convoys

| Concept | Description |
|---------|-------------|
| **Bead** | An atomic unit of work. Has a unique ID like `gt-abc12`, a title, description, priority, status, and its own Git branch (`bead/<id>`) and worktree. |
| **Convoy** | A group of beads dispatched together in one run. |
| **Bead status lifecycle** | `pending` → `in_progress` → `done` → `merging` → `merged` (or `failed` / `rejected`) |

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/JJKW1984/Gastown.git
cd Gastown
pip install -e ".[dev]"
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and set your LLM provider credentials.
```

Minimum required:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Initialize a rig

A _rig_ wraps a git repository that PoleCATs will work on.

```bash
# Initialize from the current directory
gastown init .

# Or specify a path and name
gastown init /path/to/my-project --name "My Project"
```

### 4. Run a goal

```bash
gastown run "Add input validation to all API endpoints and write unit tests"
```

The CLI shows a live table of bead statuses, then prints the Mayor's review on completion.

### 5. (Optional) Web UI

```bash
gastown serve
# Open http://127.0.0.1:8000
```

---

## Architecture Overview

```
┌───────────────────────────────────────────────────────────────┐
│                          User / CLI / Web                      │
└────────────────────────┬──────────────────────────────────────┘
                         │  goal + rig
                         ▼
               ┌─────────────────┐
               │  GastownOrchestrator  │
               └────────┬────────┘
                        │
             ┌──────────▼──────────┐
             │  Mayor (LLM)        │
             │  decompose → beads  │
             └──────────┬──────────┘
                        │  beads[]
          ┌─────────────▼──────────────────────┐
          │         asyncio.Semaphore(N)        │
          │  ┌──────────┐  ┌──────────┐        │
          │  │ PoleCAT  │  │ PoleCAT  │  ...   │
          │  │ bead/1   │  │ bead/2   │        │
          │  └────┬─────┘  └────┬─────┘        │
          │       │  WitnessEvents              │
          │       └──────┬───────┘             │
          │              ▼                      │
          │         event_queue                 │
          │              │                      │
          │     ┌────────▼────────┐             │
          │     │   Witness       │             │
          │     │  (watchdog)     │             │
          │     └─────────────────┘             │
          └─────────────────────────────────────┘
                        │ completed beads
               ┌────────▼─────────┐
               │  Refinery        │
               │  (merge queue)   │
               └────────┬─────────┘
                        │ merged / rejected
               ┌────────▼─────────┐
               │  Mayor review    │
               └──────────────────┘
                        │ RunResult
               ┌────────▼─────────┐
               │  SQLite (GastownDB)│
               └──────────────────┘
```

Each PoleCAT works in an isolated **Git worktree** (`<repo>/.worktrees/<bead-id>/`) on a dedicated branch (`bead/<id>`), so agents cannot interfere with each other.

---

## Configuration

All configuration is via environment variables (or `.env` in the project root).

### Core variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GASTOWN_DB_PATH` | `gastown.db` | SQLite database file path |
| `GASTOWN_HOST` | `127.0.0.1` | Web server bind host |
| `GASTOWN_PORT` | `8000` | Web server port |
| `GASTOWN_MAX_CONCURRENT_POLECATS` | `4` | Max simultaneous PoleCAT workers |
| `GASTOWN_STUCK_TIMEOUT_SECONDS` | `120` | Seconds before Witness nudges a silent PoleCAT |
| `GASTOWN_MODEL` | `anthropic/claude-sonnet-4-6` | LiteLLM model string |

### LLM Provider credentials

| Provider | Environment variable(s) |
|----------|------------------------|
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Azure OpenAI | `AZURE_API_KEY`, `AZURE_API_BASE`, `AZURE_API_VERSION` |
| Ollama (local) | `OLLAMA_API_BASE` (default `http://localhost:11434`) |

See `docs/CONFIG.md` for the full reference including precedence rules and example setups.

---

## Usage Examples

### CLI

```bash
# Basic run
gastown run "Refactor the database module to use connection pooling"

# Override concurrency
gastown run "Add logging to all endpoints" --max-concurrent 8

# Specify rig explicitly
gastown run "Fix all type errors" --rig my-project-abc123

# Skip confirmation prompt
gastown run "Update dependencies" --yes

# Show bead status
gastown status

# List beads (optionally filter by status)
gastown beads --status-filter done

# View logs for a bead
gastown logs gt-abc12

# Start web server
gastown serve --host 0.0.0.0 --port 8080
```

### Model override

```bash
GASTOWN_MODEL=openai/gpt-4o gastown run "Write unit tests for storage.py"
```

### Web API

```bash
# Create a rig
curl -X POST http://localhost:8000/api/rigs \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/repo", "name": "My Project"}'

# Start a run
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"goal": "Add pagination to the list endpoints", "rig_id": "my-project-abc123"}'

# Check run status
curl http://localhost:8000/api/runs/<run_id>
```

---

## PoleCAT Tool Reference

Each PoleCAT has access to five tools:

| Tool | Description |
|------|-------------|
| `read_file` | Read a file by relative path. Files over 50 000 chars are truncated. |
| `write_file` | Write full content to a file. Parent directories are created automatically. |
| `list_directory` | List files/directories at a path. |
| `run_command` | Run any shell command (git, python, pytest, etc.) with a 30 s timeout. |
| `done_signal` | Signal task completion. Triggers a git commit and bead status update. |

All paths are relative to the bead's worktree directory. Path traversal outside the worktree is blocked.

---

## Deployment

Gastown ships with a GitHub Actions workflow for Azure Web App for Containers using **OpenTofu** (open-source Terraform fork):

- Workflow: `.github/workflows/azure-webapp-deploy.yml`
- Infrastructure: `infra/terraform/`

### Deployment quick reference

1. Set the required GitHub secrets and variables (see `docs/azure-webapp-terraform-deploy.md`).
2. Run the `Azure Web App Deploy (OpenTofu / Terraform)` workflow.
3. Set `apply=true` to provision and deploy.

See the full deployment guide: [`docs/azure-webapp-terraform-deploy.md`](docs/azure-webapp-terraform-deploy.md)

Local OpenTofu development: [`docs/opentofu-local-dev.md`](docs/opentofu-local-dev.md)

---

## Testing

82 tests; all passing. Three categories:

| Category | What it covers |
|----------|----------------|
| **Unit** | Models, storage CRUD, PoleCAT tool safety, Witness logic, Mayor decomposition |
| **Security** | Path traversal blocking, SQL injection protection, static analysis (bandit) |
| **Performance** | SQLite throughput, model serialization speed, concurrency benchmarks |

```bash
# All tests
pytest

# With coverage
pytest --cov=gastown --cov-report=term-missing

# Performance benchmarks only
pytest tests/performance/ -v

# Security tests only
pytest tests/security/ -v
```

---

## Performance

Measured on local hardware with `pytest-benchmark`. See `PERFORMANCE_REPORT.md` for full details and methodology.

| Scenario | Throughput | p95 latency |
|----------|-----------|-------------|
| Light load | 1,436 req/s | 0.7 ms |
| Medium load | 1,943 req/s | 73.9 ms |
| Heavy load | 1,202 req/s | 389.9 ms |
| Very heavy load | 633 req/s | 134.0 ms |

See `docs/PERFORMANCE_TUNING.md` for optimization guidance.

---

## Troubleshooting

| Symptom | Quick fix |
|---------|-----------|
| `ANTHROPIC_API_KEY not set` | Add key to `.env` and restart |
| PoleCAT stuck / bead stays `in_progress` | Lower `GASTOWN_STUCK_TIMEOUT_SECONDS`; Witness will nudge automatically |
| `database is locked` | Only one Gastown process should own the DB file |
| `git worktree add failed` | Ensure repo has at least one commit; `gastown init` handles this |
| Web dashboard shows no events | Check WebSocket connection; see `docs/TROUBLESHOOTING.md` |

Full troubleshooting guide: [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)

---

## Contributing

1. Fork the repo and create a branch.
2. `pip install -e ".[dev]"` to get test/lint dependencies.
3. Write tests for new behaviour.
4. Run `pytest` and `bandit -r gastown/` before opening a PR.
5. Keep beads small — the Mayor's rule applies to contributors too.

---

## Planned Features / Known Limits

**Planned**
- Authentication / API keys for the web API.
- Bead dependency ordering (sequential dispatch when `depends_on` is set).
- Support for multi-repo rigs.
- Persistent WebSocket reconnection.
- UI bead detail view.

**Current Limits**
- SQLite is single-writer; horizontal scaling requires migrating to PostgreSQL.
- PoleCAT context window is bounded by `MAX_TOKENS = 8096`; very large files are truncated.
- `run_command` timeout is 30 s; long-running build steps may time out.
- No built-in authentication; do not expose the web API on public networks without a reverse proxy.
