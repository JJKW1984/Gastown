# Troubleshooting

This guide covers the most common installation, runtime, and operational issues with Gastown.

---

## Contents

- [Installation Issues](#installation-issues)
- [API Key and Provider Errors](#api-key-and-provider-errors)
- [Rate Limits and Timeouts](#rate-limits-and-timeouts)
- [Agent Stuck / Nudge Issues](#agent-stuck--nudge-issues)
- [Tool Timeouts](#tool-timeouts)
- [Database Issues](#database-issues)
- [Git and Worktree Errors](#git-and-worktree-errors)
- [Web Dashboard and WebSocket Issues](#web-dashboard-and-websocket-issues)
- [Performance and Memory Issues](#performance-and-memory-issues)
- [Nuclear Reset](#nuclear-reset)
- [What to Collect for Bug Reports](#what-to-collect-for-bug-reports)

---

## Installation Issues

### `pip install` fails with Python version error

Gastown requires Python 3.11+.

```bash
python --version    # must be 3.11 or newer
```

If you have multiple Python versions, use `python3.11 -m pip install -e ".[dev]"` or a virtual environment:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### `litellm` or `pydantic` version conflict

Ensure you are installing inside a clean virtual environment:

```bash
pip install --upgrade pip
pip install -e ".[dev]"
```

If a conflict persists, check that you are not mixing with a system site-packages:

```bash
python -c "import site; print(site.getsitepackages())"
```

### `gastown` command not found after install

The `gastown` entry point is registered by setuptools. If it is not on your PATH:

```bash
# Check where pip installed scripts
pip show -f gastown | grep "Location:"
# Typically ~/.local/bin or .venv/bin
export PATH="$HOME/.local/bin:$PATH"
```

---

## API Key and Provider Errors

### `AuthenticationError` / `Invalid API key`

1. Confirm the key exists: `echo $ANTHROPIC_API_KEY` (or the relevant variable).
2. If using `.env`, check the file is in the current working directory when you run the CLI.
3. Watch for trailing spaces or newlines: open `.env` in a text editor and verify the key value.
4. Verify the key is active in your provider's dashboard.

### `NotFoundError: LiteLLM: Model Not Found`

The `GASTOWN_MODEL` value does not match LiteLLM's expected format.

```bash
# Wrong
GASTOWN_MODEL=claude-sonnet-4-6

# Correct
GASTOWN_MODEL=anthropic/claude-sonnet-4-6
```

See `docs/CONFIG.md` for provider-specific model strings.

### Azure: `ResourceNotFound` or `DeploymentNotFound`

```bash
GASTOWN_MODEL=azure/<DEPLOYMENT_NAME>   # must match exactly what you named it in Azure AI Studio
AZURE_API_BASE=https://<resource>.openai.azure.com   # no trailing slash
AZURE_API_VERSION=2024-02-01
```

---

## Rate Limits and Timeouts

### `429 Too Many Requests`

You are sending more concurrent LLM calls than your provider tier allows. Reduce concurrency:

```bash
GASTOWN_MAX_CONCURRENT_POLECATS=2
```

Or wait for tier upgrades. See `docs/PERFORMANCE_TUNING.md` for per-provider guidelines.

### `httpx.ReadTimeout` / `asyncio.TimeoutError` from LiteLLM

The provider is taking too long to respond. This can be transient. If it recurs:

1. Check provider status pages.
2. Switch to a faster model (`claude-haiku-4-5`, `gpt-4o-mini`).
3. Reduce `MAX_TOKENS` in `gastown/agents/base.py` (a lower ceiling can speed up short responses).

---

## Agent Stuck / Nudge Issues

### Bead stuck in `in_progress` for a long time

The Witness sends up to 3 nudges after `GASTOWN_STUCK_TIMEOUT_SECONDS` of silence, then cancels. If you want faster reaction:

```bash
GASTOWN_STUCK_TIMEOUT_SECONDS=60    # nudge after 60 s
```

If the agent is actively making LLM calls but making no useful progress, it may be in a tool-call loop. This is normal; the PoleCAT has a hard limit of 30 tool calls (`MAX_TOOL_CALLS`) and will mark the bead done at that point.

### Bead repeatedly marked `failed`

Check the bead's event log:

```bash
gastown logs <bead_id>
# or via API:
curl http://localhost:8000/api/beads/<bead_id>/logs
```

Common causes:

| `event_type` in log | Likely cause |
|---------------------|-------------|
| `worktree_error` | Git repo missing commits; run `gastown init` again |
| `polecat_failed` | LLM API error; check API key and rate limits |
| `witness_cancel` | Agent exceeded nudge limit; task complexity may be too high — try splitting the goal |

### Witness not detecting stuck agents

Verify `GASTOWN_STUCK_TIMEOUT_SECONDS` is set correctly and that the Witness task is actually running. If you are running a custom orchestrator, confirm `witness.monitor()` is being awaited as a task.

---

## Tool Timeouts

### `[timeout after 30s]` in bead logs

The `run_command` tool has a 30-second hard timeout. If your commands (e.g., `pytest`, `npm install`) take longer:

1. Split the bead so the long command is in its own step.
2. Consider pre-installing dependencies before the run starts so the PoleCAT's commands are faster.
3. The timeout is set in `polecat.py:COMMAND_TIMEOUT = 30`. You can change this value if you control the deployment.

### `path traversal blocked` errors

A PoleCAT attempted to read or write a file outside its worktree. This is a security feature, not a bug. The bead's description likely asked it to touch a file in a different location. Review the bead's description and ensure paths are relative to the project root.

---

## Database Issues

### `database is locked`

Only one Gastown process (CLI or web server) should own the SQLite database at a time. SQLite WAL mode is used, but simultaneous writers from separate processes still cause locks.

1. Find other Gastown processes: `pgrep -a -f gastown`
2. Stop duplicate processes.
3. If a crash left a stale lock file, delete it: `rm gastown.db-wal gastown.db-shm`

### `sqlite3.OperationalError: no such table: beads`

The database was not initialized. This can happen if:

- You pointed `GASTOWN_DB_PATH` to a new location without running any command that calls `db.initialize()`.
- The database file was deleted manually.

Fix: run any Gastown command (e.g., `gastown status`) which calls `db.initialize()` automatically.

### Database corruption

If the DB file is corrupt (after a crash or disk issue):

```bash
# Check integrity
sqlite3 gastown.db "PRAGMA integrity_check;"

# If corrupted, start fresh (data loss)
mv gastown.db gastown.db.bak
```

The database contains orchestration state (bead statuses, events), not your code. The code is in git, so data loss here is recoverable.

---

## Git and Worktree Errors

### `git worktree add failed` / `fatal: not a git repository`

Gastown requires the rig's repo path to be a valid git repository with at least one commit. Run:

```bash
gastown init /path/to/repo
```

`init` calls `gt_ensure_initial_commit()` which creates an empty initial commit if needed.

### Leftover `.worktrees/` directories

If a run was killed mid-execution, worktrees may not have been cleaned up:

```bash
cd /path/to/repo
git worktree list           # see all registered worktrees
git worktree prune          # remove stale entries
rm -rf .worktrees/          # remove directories
```

### Merge conflicts in the Refinery

The Refinery uses a bisecting algorithm to isolate conflicting beads. Conflicting beads are marked `rejected` and the rest are merged. To see which bead was rejected:

```bash
gastown beads --status-filter rejected
gastown logs <rejected_bead_id>
```

To recover: manually resolve the conflict on the bead's branch and re-merge, or re-run the goal with a more specific description.

### `git: 'worktree' is not a git command`

You are running an old version of git. Git worktrees require git 2.5+.

```bash
git --version    # must be 2.5 or newer
```

---

## Web Dashboard and WebSocket Issues

### Dashboard loads but shows no rigs or runs

The API is returning empty lists. Create a rig first:

```bash
curl -X POST http://localhost:8000/api/rigs \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/repo", "name": "My Project"}'
```

### WebSocket events stop arriving mid-run

1. Check that the server process is still running.
2. The WebSocket queue is per-run and in-memory. If the server restarts during a run, the queue is lost. Poll `GET /api/runs/{run_id}` instead.
3. If you are behind a load balancer or reverse proxy, ensure WebSocket upgrades are configured (e.g., nginx: `proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";`).

### `WebSocket connection to 'ws://...' failed`

- Confirm the server is running: `curl http://localhost:8000/api/rigs`
- The run ID must exist before connecting. Start the run first, then connect.
- If using HTTPS, use `wss://` not `ws://`.

---

## Performance and Memory Issues

### Runs are slow

1. Check which model you are using. `claude-haiku-4-5` or `gpt-4o-mini` are significantly faster for simple tasks.
2. Check your provider's latency; US-East Azure endpoints are often fastest for US users.
3. Increase `GASTOWN_MAX_CONCURRENT_POLECATS` if beads are queuing (check `in_progress` count stays < `max_concurrent`).

### Memory usage grows over time

- PoleCAT message histories are held in memory for the duration of the run. After the run completes, they are garbage collected.
- If memory keeps growing across runs, check for lingering `asyncio.Task` objects in `_active_runs` (the web app's global dict). This is a known area for improvement.
- Restart the web server periodically if running long-duration deployments.

---

## Nuclear Reset

Use this if the database state is inconsistent and you want to start fresh. **Your code (git commits) is not affected.**

```bash
# 1. Stop all Gastown processes
pkill -f gastown

# 2. Remove the database
rm -f gastown.db gastown.db-wal gastown.db-shm

# 3. Remove any leftover worktrees
cd /path/to/your/repo
git worktree prune
rm -rf .worktrees/

# 4. Re-initialize
gastown init .
```

---

## What to Collect for Bug Reports

When filing a bug report, include:

1. **Gastown version**: `pip show gastown`
2. **Python version**: `python --version`
3. **OS and git version**: `uname -a && git --version`
4. **Relevant env vars** (redact API keys): `GASTOWN_MODEL`, `GASTOWN_MAX_CONCURRENT_POLECATS`, `GASTOWN_STUCK_TIMEOUT_SECONDS`
5. **Bead event log**: `gastown logs <bead_id>` or `curl http://localhost:8000/api/beads/<bead_id>/logs`
6. **Database integrity**: `sqlite3 gastown.db "PRAGMA integrity_check;"`
7. **Any traceback** from the server or CLI output
8. **Goal description** that triggered the issue (you can redact sensitive content)
