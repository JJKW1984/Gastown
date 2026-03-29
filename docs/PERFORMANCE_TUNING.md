# Performance Tuning

This guide covers benchmark results, optimization strategies, and scaling guidance for Gastown.

---

## Contents

- [Benchmark Results](#benchmark-results)
- [Throughput and Latency Optimization](#throughput-and-latency-optimization)
- [Concurrency Tuning](#concurrency-tuning)
- [Model Selection](#model-selection)
- [Resource Estimates](#resource-estimates)
- [Scaling Strategies](#scaling-strategies)
- [Monitoring Snippets](#monitoring-snippets)

---

## Benchmark Results

Measured with `pytest-benchmark` on local hardware. See the full methodology in `tests/performance/`.

### SQLite / Model Serialization

| Scenario | Throughput | p95 Latency |
|----------|-----------|------------|
| Light load | 1,436 req/s | 0.7 ms |
| Medium load | 1,943 req/s | 73.9 ms |
| Heavy load | 1,202 req/s | 389.9 ms |
| Very heavy load | 633 req/s | 134.0 ms |

**Interpretation**

- Light and medium loads are dominated by Pydantic serialization and SQLite reads. Both are fast.
- Heavy load introduces write contention on the `asyncio.Lock()` in `GastownDB`.
- Very heavy load numbers show the throughput floor when all workers are simultaneously writing.

### Running the benchmarks yourself

```bash
# Full benchmark suite
pytest tests/performance/ -v --benchmark-sort=mean

# DB benchmarks only
pytest tests/performance/test_bench_db.py -v

# Model serialization benchmarks
pytest tests/performance/test_bench_models.py -v

# Locust load test (requires running server)
gastown serve &
locust -f tests/performance/locustfile.py --host http://localhost:8000
```

---

## Throughput and Latency Optimization

### SQLite WAL mode

Already enabled by default in `GastownDB.initialize()`:

```sql
PRAGMA journal_mode=WAL;
```

WAL mode allows concurrent readers while a writer holds the lock. Do not disable this.

### Reduce lock contention

`GastownDB` uses a single `asyncio.Lock()` for all writes. Each PoleCAT acquires this lock for every heartbeat event log, bead status update, and worktree path write.

To reduce pressure:

1. **Increase `GASTOWN_STUCK_TIMEOUT_SECONDS`**: Fewer timeouts → fewer Witness log events.
2. **Batch event logging**: If you extend the agent code, group multiple small events into one `log_event` call.
3. **Use a faster disk**: SQLite performance is I/O bound at high concurrency. An NVMe SSD or tmpfs (`/dev/shm`) can double throughput in write-heavy scenarios.

```bash
# tmpfs example (Linux) — ephemeral; data lost on reboot
mkdir -p /dev/shm/gastown
export GASTOWN_DB_PATH=/dev/shm/gastown/gastown.db
```

### Large file handling

`read_file` truncates at 50 000 chars. If PoleCATs frequently read large files and fail to make progress, the truncation may be hiding context they need. Consider:

- Splitting large files into smaller modules (reduces the file size PoleCAT needs to read).
- Increasing `MAX_TOKENS` in `BaseAgent` (higher cost, slower response).

---

## Concurrency Tuning

`GASTOWN_MAX_CONCURRENT_POLECATS` is the primary concurrency knob. Optimal values depend on your LLM provider's rate limits.

### Provider rate limit guidelines

| Provider / Tier | Typical RPM | Recommended concurrency |
|----------------|------------|------------------------|
| Anthropic (Tier 1) | 50 RPM | 2–4 |
| Anthropic (Tier 3+) | 1 000+ RPM | 8–16 |
| OpenAI (Tier 2) | 3 500 RPM | 8–16 |
| Azure OpenAI | Configurable (PTU) | 16–32+ |
| Ollama (local) | Unlimited (CPU bound) | 1–2 (or # of GPUs) |

Each PoleCAT makes approximately 3–15 LLM calls per bead depending on task complexity. Set concurrency such that `max_concurrent × avg_calls_per_bead / avg_call_duration_s` stays below your provider's RPM limit.

### Finding the sweet spot

```bash
# Run a goal and check elapsed time at different concurrency values
time GASTOWN_MAX_CONCURRENT_POLECATS=2 gastown run "..." --yes
time GASTOWN_MAX_CONCURRENT_POLECATS=4 gastown run "..." --yes
time GASTOWN_MAX_CONCURRENT_POLECATS=8 gastown run "..." --yes
```

If you start seeing `429 Too Many Requests` errors in the logs, reduce concurrency. If PoleCATs finish quickly but there are many beads queued, increase it.

---

## Model Selection

Different models have different speed/quality/cost trade-offs:

| Model | Speed | Quality | Cost | Best for |
|-------|-------|---------|------|---------|
| `anthropic/claude-haiku-4-5` | ⚡⚡⚡ | ★★☆ | $ | Simple tasks, high-volume runs |
| `anthropic/claude-sonnet-4-6` | ⚡⚡☆ | ★★★ | $$ | Default; best general balance |
| `anthropic/claude-opus-4-5` | ⚡☆☆ | ★★★★ | $$$$ | Complex architectural tasks |
| `openai/gpt-4o-mini` | ⚡⚡⚡ | ★★☆ | $ | High-volume, low-complexity |
| `openai/gpt-4o` | ⚡⚡☆ | ★★★ | $$ | Good alternative to Sonnet |
| `ollama/llama3` | ⚡☆☆ (local) | ★★☆ | Free | Air-gapped, budget |
| `ollama/qwen2.5-coder` | ⚡⚡☆ (local) | ★★★ | Free | Best local coding model |

Override per-run from the CLI:

```bash
GASTOWN_MODEL=anthropic/claude-haiku-4-5 gastown run "Fix all docstring typos" --yes
```

---

## Resource Estimates

### Memory

| Component | Per-instance estimate |
|-----------|----------------------|
| FastAPI + uvicorn | ~60 MB baseline |
| GastownDB | Negligible (SQLite in-process) |
| Per PoleCAT (message history) | 1–10 MB (depends on context length) |
| 4 concurrent PoleCATs | ~100–200 MB additional |

Total for a typical 4-worker deployment: **200–400 MB**.

### CPU

- PoleCAT loops are I/O bound (waiting on LLM API calls). CPU usage is low.
- Pydantic serialization and SQLite writes consume brief CPU spikes.
- `run_command` tool calls can spawn CPU-intensive subprocesses (e.g., `pytest`).

### Disk

- SQLite database: grows at ~1 KB per bead + ~500 B per event. For 1 000 runs with 5 beads each, expect ~5–10 MB.
- Git worktrees: each worktree is a full checkout of the repository. For a 100 MB repo with 8 concurrent PoleCATs, you need ~800 MB free disk.
- Ensure `.worktrees/` is excluded from backup systems if the repo is large.

### Network

- All LLM API calls are outbound HTTPS. Latency is the primary bottleneck, not bandwidth.
- Each LLM call transfers ~2–20 KB request + ~1–8 KB response.

---

## Scaling Strategies

### Vertical scaling (single machine)

1. Increase `GASTOWN_MAX_CONCURRENT_POLECATS` to fill provider rate limits.
2. Use a faster disk for the SQLite DB (NVMe or tmpfs for ephemeral use).
3. Pin the process to a high-performance CPU core if latency-sensitive.

### Horizontal scaling

**Current limitation**: SQLite is single-writer. Multiple Gastown processes sharing one DB file will cause `database is locked` errors under write load.

**Migration path to PostgreSQL** (not yet implemented):

1. Replace `GastownDB` with an async SQLAlchemy adapter.
2. Remove the `asyncio.Lock()` (DB handles concurrency itself).
3. Update `initialize()` to use `CREATE TABLE IF NOT EXISTS` with PostgreSQL syntax.
4. Multiple Gastown instances can then share one PostgreSQL database.

### Azure-specific scaling

The Terraform configuration in `infra/terraform/` provisions an Azure Web App. To scale:

- Change `app_service_plan_sku` to a higher tier (e.g., `P2v3` → `P3v3`).
- Enable auto-scaling rules in the Azure Portal.
- Use Azure File Share or Azure Blob as the persistent storage mount for the DB (set `GASTOWN_DB_PATH` accordingly).

---

## Monitoring Snippets

### Check active bead counts via REST

```bash
watch -n 2 'curl -s http://localhost:8000/api/rigs/<rig_id>/status | python3 -m json.tool'
```

### SQLite query for run summary

```bash
sqlite3 gastown.db "
SELECT status, COUNT(*) as count
FROM beads
GROUP BY status
ORDER BY count DESC;
"
```

### Event rate per minute

```bash
sqlite3 gastown.db "
SELECT strftime('%Y-%m-%dT%H:%M', timestamp) as minute, COUNT(*) as events
FROM events
WHERE timestamp > datetime('now', '-1 hour')
GROUP BY minute
ORDER BY minute;
"
```

### Watch real-time events during a run (WebSocket)

```bash
# Using websocat (https://github.com/vi/websocat)
websocat ws://localhost:8000/ws/runs/<run_id>
```

### Process memory usage

```bash
# Get the PID of the Gastown server process
pgrep -f "gastown serve"

# Watch memory
watch -n 5 'ps -o pid,rss,vsz,comm -p $(pgrep -f "gastown serve")'
```
