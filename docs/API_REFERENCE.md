# API Reference

Gastown exposes a FastAPI REST API and a WebSocket endpoint. The interactive OpenAPI docs are available at `http://localhost:8000/docs` when the server is running.

---

## Contents

- [Base URL](#base-url)
- [Rigs](#rigs)
- [Beads](#beads)
- [Runs](#runs)
- [WebSocket Stream](#websocket-stream)
- [Error Format](#error-format)
- [Status Codes](#status-codes)
- [cURL Examples](#curl-examples)
- [JavaScript Examples](#javascript-examples)

---

## Base URL

```
http://<host>:<port>
```

Default: `http://127.0.0.1:8000`

---

## Rigs

A **Rig** wraps a git repository. Beads and runs are always scoped to a rig.

### List rigs

```
GET /api/rigs
```

**Response 200**

```json
[
  {
    "id": "my-project-a1b2c3",
    "name": "My Project",
    "repo_path": "/home/user/my-project",
    "description": "Main application repo",
    "created_at": "2024-01-01T12:00:00+00:00"
  }
]
```

---

### Create rig

```
POST /api/rigs
```

**Request body**

```json
{
  "path": "/absolute/path/to/repo",
  "name": "My Project",
  "description": "Optional description"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `path` | string | ✅ | Absolute path; must be an existing directory |
| `name` | string | ✅ | Used to generate the rig ID |
| `description` | string | ❌ | Defaults to `""` |

**Response 201**

```json
{
  "id": "my-project-a1b2c3",
  "name": "My Project",
  "repo_path": "/absolute/path/to/repo",
  "description": "",
  "created_at": "2024-01-01T12:00:00+00:00"
}
```

**Response 400** — directory not found.

---

### Get rig

```
GET /api/rigs/{rig_id}
```

**Response 200** — same shape as the create response.

**Response 404** — rig not found.

---

### Rig status (bead counts)

```
GET /api/rigs/{rig_id}/status
```

**Response 200**

```json
{
  "pending": 0,
  "in_progress": 2,
  "done": 3,
  "merged": 5,
  "failed": 1
}
```

---

### List beads for a rig

```
GET /api/rigs/{rig_id}/beads[?status=<status>]
```

**Query parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Optional filter: `pending`, `in_progress`, `done`, `failed`, `merged`, `rejected`, `merging`, `blocked` |

**Response 200** — array of Bead objects (see [Beads](#beads)).

---

## Beads

### Get bead

```
GET /api/beads/{bead_id}
```

**Response 200**

```json
{
  "id": "gt-abc12",
  "rig_id": "my-project-a1b2c3",
  "title": "Add input validation",
  "description": "Add Pydantic validators to all POST endpoints",
  "status": "merged",
  "priority": 0,
  "convoy_id": "convoy-deadbeef",
  "polecat_id": null,
  "branch_name": "bead/gt-abc12",
  "worktree_path": "/home/user/my-project/.worktrees/gt-abc12",
  "created_at": "2024-01-01T12:00:00+00:00",
  "updated_at": "2024-01-01T12:05:00+00:00",
  "metadata": {
    "estimated_files": ["src/api/endpoints.py"],
    "depends_on": [],
    "done_summary": "Added Pydantic validation to 3 endpoints"
  }
}
```

**Response 404** — bead not found.

---

### Get bead logs (event history)

```
GET /api/beads/{bead_id}/logs
```

**Response 200**

```json
[
  {
    "id": 42,
    "bead_id": "gt-abc12",
    "polecat_id": "polecat-1a2b3c4d",
    "event_type": "polecat_start",
    "details": "PoleCAT polecat-1a2b3c4d started",
    "timestamp": "2024-01-01T12:00:00+00:00"
  },
  {
    "id": 43,
    "bead_id": "gt-abc12",
    "polecat_id": "polecat-1a2b3c4d",
    "event_type": "polecat_done",
    "details": "PoleCAT done: Added Pydantic validation to 3 endpoints",
    "timestamp": "2024-01-01T12:04:50+00:00"
  }
]
```

Common event types:

| `event_type` | Source | Meaning |
|-------------|--------|---------|
| `decomposed` | Mayor | Goal decomposed into N beads |
| `slang` | Mayor | Convoy created and beads dispatched |
| `worktree_error` | Orchestrator | Worktree creation failed for this bead |
| `polecat_start` | PoleCAT | Worker started |
| `polecat_done` | PoleCAT | Worker finished successfully |
| `polecat_failed` | PoleCAT | Worker encountered an error |
| `witness_nudge` | Witness | Stuck agent nudged |
| `witness_cancel` | Witness | Stuck agent cancelled after max nudges |
| `witness_done` | Witness | Agent completion witnessed |
| `refinery_merged` | Refinery | Branch merged into main |
| `refinery_reject` | Refinery | Branch rejected due to conflict |

---

## Runs

### Start a run

```
POST /api/runs
```

Returns `202 Accepted` immediately. The run executes asynchronously. Poll `GET /api/runs/{run_id}` or connect to the WebSocket for real-time updates.

**Request body**

```json
{
  "goal": "Add rate limiting to all API endpoints",
  "rig_id": "my-project-a1b2c3",
  "max_concurrent": 4
}
```

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `goal` | string | ✅ | — | Plain-English engineering goal |
| `rig_id` | string | ✅ | — | Must exist |
| `max_concurrent` | integer | ❌ | `4` | Overrides `GASTOWN_MAX_CONCURRENT_POLECATS` for this run |

**Response 202**

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "started_at": "2024-01-01T12:00:00+00:00"
}
```

**Response 404** — rig not found.

---

### Get run status

```
GET /api/runs/{run_id}
```

**Response 200 — running**

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running",
  "result": null,
  "started_at": "2024-01-01T12:00:00+00:00",
  "error": null
}
```

**Response 200 — completed**

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "result": {
    "run_id": "550e8400-e29b-41d4-a716-446655440000",
    "rig_id": "my-project-a1b2c3",
    "goal": "Add rate limiting to all API endpoints",
    "beads": [ /* Bead objects */ ],
    "merged": ["gt-abc12", "gt-def34"],
    "rejected": [],
    "mayor_review": "Both rate-limiting beads merged cleanly. Tests pass.",
    "started_at": "2024-01-01T12:00:00+00:00",
    "finished_at": "2024-01-01T12:08:22+00:00"
  },
  "started_at": "2024-01-01T12:00:00+00:00",
  "error": null
}
```

**Response 200 — failed**

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "failed",
  "result": null,
  "started_at": "2024-01-01T12:00:00+00:00",
  "error": "Mayor produced no beads for this goal."
}
```

**Response 404** — run not found.

---

## WebSocket Stream

Connect before or immediately after starting a run to receive real-time events.

```
WS /ws/runs/{run_id}
```

### Message types

**Heartbeat / progress event**

```json
{
  "polecat_id": "polecat-1a2b3c4d",
  "bead_id": "gt-abc12",
  "event_type": "heartbeat",
  "details": "LLM round complete (tool_calls=3)",
  "timestamp": "2024-01-01T12:01:00+00:00"
}
```

`event_type` values: `heartbeat`, `done`, `failed`, `nudge`, `stuck`.

**Run complete**

```json
{
  "event_type": "run_complete",
  "result": { /* RunResult object */ }
}
```

**Run failed**

```json
{
  "event_type": "run_failed",
  "error": "..."
}
```

**Error (run not found)**

```json
{
  "error": "Run 550e8400-... not found"
}
```

After sending `run_complete` or `run_failed`, the server closes the connection.

### Notes

- The server does not buffer events that occurred before you connected. Connect immediately after `POST /api/runs` to avoid missing early events.
- If you disconnect, events are discarded. Reconnecting after a run finishes will receive `{"error": "Run ... not found"}` because the queue has been cleaned up.
- Use `GET /api/runs/{run_id}` to poll the final result if you cannot maintain a WebSocket connection.

---

## Error Format

All HTTP errors return JSON:

```json
{
  "detail": "Rig 'my-project-abc' not found"
}
```

---

## Status Codes

| Code | Meaning |
|------|---------|
| 200 | OK |
| 201 | Created (rig) |
| 202 | Accepted (run started asynchronously) |
| 400 | Bad request (invalid path, missing field) |
| 404 | Resource not found |
| 422 | Validation error (Pydantic) |
| 500 | Internal server error |

---

## cURL Examples

```bash
# List rigs
curl http://localhost:8000/api/rigs

# Create a rig
curl -X POST http://localhost:8000/api/rigs \
  -H "Content-Type: application/json" \
  -d '{"path": "/home/user/my-project", "name": "My Project"}'

# Start a run
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"goal": "Add pagination to list endpoints", "rig_id": "my-project-a1b2c3"}'

# Poll run status
curl http://localhost:8000/api/runs/550e8400-e29b-41d4-a716-446655440000

# Get bead logs
curl http://localhost:8000/api/beads/gt-abc12/logs

# Get rig bead counts
curl http://localhost:8000/api/rigs/my-project-a1b2c3/status

# List only merged beads
curl "http://localhost:8000/api/rigs/my-project-a1b2c3/beads?status=merged"
```

---

## JavaScript Examples

### Start a run and stream events

```js
async function runGoal(rigId, goal) {
  // Start the run
  const startRes = await fetch("http://localhost:8000/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ goal, rig_id: rigId }),
  });
  const { run_id } = await startRes.json();

  // Stream events over WebSocket
  const ws = new WebSocket(`ws://localhost:8000/ws/runs/${run_id}`);

  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);
    if (msg.event_type === "run_complete") {
      console.log("Run finished:", msg.result.mayor_review);
      ws.close();
    } else if (msg.event_type === "run_failed") {
      console.error("Run failed:", msg.error);
      ws.close();
    } else {
      console.log(`[${msg.bead_id}] ${msg.event_type}: ${msg.details}`);
    }
  };

  ws.onerror = (err) => console.error("WebSocket error:", err);
}
```

### Poll for completion

```js
async function waitForRun(runId, intervalMs = 2000) {
  while (true) {
    const res = await fetch(`http://localhost:8000/api/runs/${runId}`);
    const data = await res.json();
    if (data.status === "completed") return data.result;
    if (data.status === "failed") throw new Error(data.error);
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
```
