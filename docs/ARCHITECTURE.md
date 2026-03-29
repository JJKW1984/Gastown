# Architecture

This document explains how Gastown's components fit together at a level of detail suitable for contributors and operators.

---

## Contents

- [Agent Roles and Responsibilities](#agent-roles-and-responsibilities)
- [State Machines](#state-machines)
- [Queues and Communication](#queues-and-communication)
- [Concurrency, Locks, and Task Cancellation](#concurrency-locks-and-task-cancellation)
- [Error Handling and Recovery](#error-handling-and-recovery)
- [Git Worktree Strategy](#git-worktree-strategy)
- [Merge Queue (Bors-style Bisect)](#merge-queue-bors-style-bisect)
- [Data Schema](#data-schema)
- [Performance Considerations](#performance-considerations)

---

## Agent Roles and Responsibilities

### GastownOrchestrator

`gastown/orchestrator.py`

The central coordinator that both the CLI and web app delegate to. A single `run()` call executes the full pipeline:

1. Ensure the repo has at least one commit (`gt_ensure_initial_commit`).
2. Call **Mayor.decompose** → list of `Bead` objects persisted to SQLite.
3. Call **Mayor.sling** → create convoy, mark beads `in_progress`.
4. For each bead: `gt_setup_worktree` → create isolated branch + directory.
5. Spin up an `asyncio.Semaphore`-bounded set of **PoleCAT** tasks.
6. Run **Witness** concurrently consuming the shared `event_queue`.
7. `await asyncio.gather(*active_tasks.values())` — wait for all PoleCATs.
8. Call **Refinery.process_completed_beads** — merge branches.
9. Tear down worktrees.
10. Call **Mayor.review_results** — produce summary string.
11. Mark merged beads `MERGED`; build and return `RunResult`.

### Mayor

`gastown/agents/mayor.py`

- **Decompose**: single LLM call using a `decompose_goal` tool (forced function call) that returns a `DecompositionResult` JSON schema. Falls back to raw JSON parse if tool calls are not supported.
- **Sling**: creates a `Convoy` record and flips all bead statuses to `in_progress`.
- **Review**: another LLM call after merge to summarize what was accomplished.

The Mayor never uses filesystem tools. It only reads the `git ls-files` output passed in the decompose prompt.

### PoleCAT

`gastown/agents/polecat.py`

Ephemeral worker for a single bead. Key loop invariants:

| Constant | Value | Meaning |
|----------|-------|---------|
| `MAX_TOOL_CALLS` | 30 | Hard ceiling on tool invocations per bead |
| `WRAP_UP_THRESHOLD` | 25 | Injects a wrap-up hint at this count |
| `COMMAND_TIMEOUT` | 30 | Seconds before `run_command` times out |

The message history is maintained in-process; no external session. The assistant message is reconstructed manually from `response.choices[0].message` to ensure `tool_calls` are preserved correctly across providers.

### Witness

`gastown/agents/witness.py`

Runs as an `asyncio.Task` for the duration of a run. It:

- Drains `event_queue` with a `POLL_INTERVAL` (10 s) timeout.
- Tracks `_last_heartbeat[bead_id]` per agent.
- On timeout: calls `_check_for_stuck()` which sends a nudge or cancels.

| Constant | Value |
|----------|-------|
| `STUCK_TIMEOUT_DEFAULT` | 120 s |
| `MAX_NUDGES` | 3 |
| `POLL_INTERVAL` | 10 s |

### Refinery

`gastown/agents/refinery.py`

Pure git subprocess logic. No LLM calls.

---

## State Machines

### Bead Lifecycle

```
                   ┌──────────┐
                   │ pending  │
                   └────┬─────┘
                        │ Mayor.sling()
                        ▼
                 ┌──────────────┐
                 │ in_progress  │◄──── worktree created
                 └──────┬───────┘
                        │
              ┌─────────┴──────────┐
              │                    │
              ▼                    ▼
        ┌──────────┐         ┌──────────┐
        │   done   │         │  failed  │ ◄── exception / max nudges
        └────┬─────┘         └──────────┘
             │ Refinery
    ┌────────┴──────────┐
    │                   │
    ▼                   ▼
┌─────────┐       ┌──────────┐
│ merging │       │ rejected │ ◄── merge conflict (single bead)
└────┬────┘       └──────────┘
     │
     ▼
┌────────┐
│ merged │
└────────┘
```

`blocked` is reserved for future dependency ordering.

### Run Lifecycle

```
POST /api/runs
    │
    ▼
asyncio.Task created (_run_task)
    │
    ├── orchestrator.run() ─────────────────────────────────────────┐
    │       Mayor → PoleCATs → Witness → Refinery → Mayor review    │
    │                                                                │
    ▼                                                                │
_run_results[run_id] = RunResult  ◄──────────────────────────────────┘
    │
    ▼
event_queue.put(None)   ← sentinel signals WebSocket clients
```

---

## Queues and Communication

Two queue types are used per run:

### Shared `event_queue`

`asyncio.Queue[WitnessEvent]`

- **Writers**: PoleCAT tasks (heartbeat, done, failed events).
- **Readers**: Witness task + `_drain_events` task (forwards to progress_callback / WebSocket).

A dedicated `_drain_events` coroutine runs for the duration of each run. It forwards every event to the user-supplied `progress_callback` (CLI live display or WebSocket send). Remaining events are flushed on cancellation.

### Per-bead `nudge_queues`

`dict[bead_id, asyncio.Queue]`

- **Writer**: Witness (`asyncio.Queue.put("nudge")`).
- **Reader**: PoleCAT (checked at the top of each tool loop iteration).

A nudge inserts a user message into the PoleCAT's conversation history telling it to continue or call `done_signal`.

---

## Concurrency, Locks, and Task Cancellation

### asyncio.Semaphore

`asyncio.Semaphore(max_concurrent)` limits how many PoleCATs run simultaneously. The semaphore is acquired inside `_run_polecat()` with `async with semaphore:`, so tasks are created immediately but block on entry until a slot is free.

### Database lock

`GastownDB` uses a single `asyncio.Lock()` (`self._lock`) wrapped around every write operation. SQLite `WAL` mode is enabled for better read concurrency.

### Task cancellation

If the Witness exhausts nudges for a bead, it calls `task.cancel()` on the corresponding asyncio task. The PoleCAT's `except asyncio.CancelledError` (implicit in `asyncio.gather`) propagates upward; the Witness has already marked the bead `FAILED` in SQLite.

The Witness task itself is cancelled after `asyncio.gather(*active_tasks.values())` completes, using `witness_task.cancel()` + `await witness_task` with `CancelledError` caught.

---

## Error Handling and Recovery

| Layer | What can fail | How it's handled |
|-------|--------------|-----------------|
| Mayor decompose | LLM call fails or returns invalid JSON | `RuntimeError` raised; run aborts with empty result |
| gt_setup_worktree | `git worktree add` fails | Bead status set to `FAILED`; event logged; other beads continue |
| PoleCAT tool call | Filesystem error, command timeout | Tool returns error string; PoleCAT continues loop |
| PoleCAT LLM call | API error | Exception propagates to outer try/except; bead marked `FAILED` |
| Refinery merge | Merge conflict | Branch rolled back; bisect algorithm isolates conflicting bead(s) |
| Worktree teardown | Already removed | `os.path.exists()` check; failure is silently swallowed |

No automatic retry is implemented at the orchestrator level. Individual beads that fail do not block other beads from completing.

---

## Git Worktree Strategy

Each bead gets an isolated working environment:

```
<repo>/
  .worktrees/
    gt-abc12/   ← PoleCAT-1 works here, on branch bead/gt-abc12
    gt-def34/   ← PoleCAT-2 works here, on branch bead/gt-def34
```

Worktree creation (`gt_setup_worktree`):

```bash
git worktree add -b bead/<id> <repo>/.worktrees/<id>
```

This requires at least one commit on the base branch, which `gt_ensure_initial_commit` guarantees.

Teardown (`gt_teardown_worktree`):

```bash
git worktree remove --force <path>
git worktree prune
```

**Security note**: PoleCAT's `_safe_path()` resolves all file paths and verifies they remain inside the worktree root. Any path that resolves outside is blocked with `ValueError`.

---

## Merge Queue (Bors-style Bisect)

The Refinery implements a simplified bors merge queue:

```
Input: [bead-A (priority 0), bead-B (priority 1), bead-C (priority 2)]

Round 1: try batch [A, B, C]
  → conflict on C
  → roll back all

Round 2: try batch [A, B]  (first half)
  → success: A and B merged

Round 3: try batch [C]  (second half → re-queued)
  → conflict: C rejected
```

Each merge attempt:

1. Records current HEAD SHA for rollback.
2. Iterates through the batch: `git checkout <main>; git merge --no-ff -m "Merge bead/<id>" <branch>`.
3. On any failure: `git merge --abort; git reset --hard <saved-SHA>`.

This means merges are always attempted onto the real working `main`/`master` branch. The Refinery detects the correct branch name via `git rev-parse --verify main` / `master`.

---

## Data Schema

SQLite database (`gastown.db` by default). WAL journal mode.

### `rigs`

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | `<name>-<6hex>` |
| `name` | TEXT | Human-readable name |
| `repo_path` | TEXT | Absolute filesystem path |
| `description` | TEXT | Optional |
| `created_at` | TEXT | ISO 8601 UTC |

### `beads`

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | `gt-<3alpha><2digit>` |
| `rig_id` | TEXT | Foreign key → rigs |
| `title` | TEXT | Short task name |
| `description` | TEXT | Full task description |
| `status` | TEXT | `BeadStatus` enum value |
| `priority` | INTEGER | 0 = highest |
| `convoy_id` | TEXT | Nullable |
| `polecat_id` | TEXT | Nullable |
| `branch_name` | TEXT | `bead/<id>` |
| `worktree_path` | TEXT | Absolute path |
| `created_at` | TEXT | ISO 8601 UTC |
| `updated_at` | TEXT | ISO 8601 UTC |
| `metadata` | TEXT | JSON blob (estimated_files, depends_on, done_summary, …) |

### `convoys`

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | `convoy-<8hex>` |
| `rig_id` | TEXT | |
| `bead_ids` | TEXT | JSON array |
| `status` | TEXT | `active` |
| `created_at` | TEXT | ISO 8601 UTC |

### `events`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | Autoincrement |
| `bead_id` | TEXT | Nullable |
| `polecat_id` | TEXT | Nullable |
| `event_type` | TEXT | e.g. `decomposed`, `polecat_start`, `witness_nudge` |
| `details` | TEXT | Free-form JSON/text; some call sites truncate to ~200–500 chars, but no DB-level cap is enforced |
| `timestamp` | TEXT | ISO 8601 UTC |

---

## Performance Considerations

- **SQLite WAL mode** allows concurrent reads during writes; throughput is dominated by lock acquisition in the asyncio layer.
- **Semaphore-bounded concurrency** prevents overloading the LLM provider's rate limits. Default is 4 concurrent PoleCATs.
- **Large file truncation**: `read_file` caps at 50 000 chars; `run_command` output caps at 10 000 chars. This keeps the LLM context window manageable.
- **Event queue draining**: the `_drain_events` coroutine runs with a 0.2 s timeout on `wait_for`, yielding to other coroutines frequently.
- **Database writes** are serialized through `asyncio.Lock`. For high-throughput scenarios (many simultaneous runs), this is the primary bottleneck.

See `docs/PERFORMANCE_TUNING.md` for tuning recommendations.
