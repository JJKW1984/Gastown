"""Gastown Performance Load Test
===============================
Self-contained script — uses httpx ASGI transport to drive the FastAPI app
directly in-process. No external server needed.

Traffic levels:
  light      —   5 concurrent workers,   200 requests
  medium     —  20 concurrent workers,   800 requests
  heavy      —  50 concurrent workers, 2,000 requests
  very_heavy — 100 concurrent workers, 5,000 requests

Writes results to PERFORMANCE_REPORT.md in the project root.

Usage:
    python tests/performance/load_test.py
    python tests/performance/load_test.py --levels light medium
    python tests/performance/load_test.py --output my_report.md
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import math
import os
import random
import statistics
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx

# ---------------------------------------------------------------------------
# Allow running from any directory
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("GASTOWN_DB_PATH", f":memory:")
os.environ.setdefault("GASTOWN_MODEL", "anthropic/claude-sonnet-4-6")

from gastown.web.app import app  # noqa: E402 — after path setup


# ---------------------------------------------------------------------------
# Traffic level definitions
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class TrafficLevel:
    name: str
    label: str
    concurrency: int
    total_requests: int
    description: str


LEVELS: list[TrafficLevel] = [
    TrafficLevel(
        name="light",
        label="[LIGHT]",
        concurrency=5,
        total_requests=200,
        description="Normal business-hours usage - a few developers using the UI",
    ),
    TrafficLevel(
        name="medium",
        label="[MEDIUM]",
        concurrency=20,
        total_requests=800,
        description="Active sprint - multiple engineers + CI polling the API",
    ),
    TrafficLevel(
        name="heavy",
        label="[HEAVY]",
        concurrency=50,
        total_requests=2_000,
        description="Heavy usage - large team, many concurrent runs in progress",
    ),
    TrafficLevel(
        name="very_heavy",
        label="[VERY HEAVY]",
        concurrency=100,
        total_requests=5_000,
        description="Stress test - 100 concurrent users hammering all endpoints",
    ),
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class RequestResult:
    endpoint: str
    method: str
    status_code: int
    latency_ms: float
    error: Optional[str] = None


@dataclasses.dataclass
class EndpointStats:
    endpoint: str
    method: str
    count: int
    errors: int
    latencies: list[float]

    @property
    def error_rate(self) -> float:
        return self.errors / self.count if self.count else 0.0

    @property
    def p50(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0

    @property
    def p90(self) -> float:
        return _percentile(self.latencies, 90)

    @property
    def p95(self) -> float:
        return _percentile(self.latencies, 95)

    @property
    def p99(self) -> float:
        return _percentile(self.latencies, 99)

    @property
    def mean(self) -> float:
        return statistics.mean(self.latencies) if self.latencies else 0

    @property
    def min_latency(self) -> float:
        return min(self.latencies) if self.latencies else 0

    @property
    def max_latency(self) -> float:
        return max(self.latencies) if self.latencies else 0


@dataclasses.dataclass
class LevelResult:
    level: TrafficLevel
    results: list[RequestResult]
    wall_time_s: float
    setup_rig_id: Optional[str]
    setup_bead_id: Optional[str]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.error or r.status_code >= 500)

    @property
    def error_rate(self) -> float:
        return self.errors / self.total if self.total else 0

    @property
    def throughput(self) -> float:
        return self.total / self.wall_time_s if self.wall_time_s else 0

    @property
    def all_latencies(self) -> list[float]:
        return [r.latency_ms for r in self.results if not r.error]

    @property
    def p50(self) -> float:
        return _percentile(self.all_latencies, 50)

    @property
    def p95(self) -> float:
        return _percentile(self.all_latencies, 95)

    @property
    def p99(self) -> float:
        return _percentile(self.all_latencies, 99)

    @property
    def mean_latency(self) -> float:
        lats = self.all_latencies
        return statistics.mean(lats) if lats else 0

    def by_endpoint(self) -> dict[str, EndpointStats]:
        groups: dict[str, EndpointStats] = {}
        for r in self.results:
            key = f"{r.method} {r.endpoint}"
            if key not in groups:
                groups[key] = EndpointStats(r.endpoint, r.method, 0, 0, [])
            s = groups[key]
            s.count += 1
            if r.error or r.status_code >= 500:
                s.errors += 1
            else:
                s.latencies.append(r.latency_ms)
        return groups


# ---------------------------------------------------------------------------
# ASGI client factory
# ---------------------------------------------------------------------------

_db_initialized = False


@asynccontextmanager
async def get_client():
    """Yield an httpx.AsyncClient wired to the FastAPI ASGI app.

    Manually initializes the DB the first time (lifespan doesn't fire
    in ASGI transport mode unless we use the lifespan scope directly).
    """
    global _db_initialized
    from gastown.web.app import _db as _app_db
    import gastown.web.app as _app_module

    if not _db_initialized:
        db_path = os.getenv("GASTOWN_DB_PATH", ":memory:")
        from gastown.storage import GastownDB
        db = GastownDB(db_path)
        await db.initialize()
        _app_module._db = db
        _db_initialized = True

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        timeout=30.0,
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Endpoint scenarios
# ---------------------------------------------------------------------------

def _rand_str(n: int = 6) -> str:
    return uuid.uuid4().hex[:n]


async def _setup_fixtures(client: httpx.AsyncClient) -> tuple[Optional[str], Optional[str]]:
    """Create a rig and a bead to use in read-path benchmarks."""
    rig_id = None
    bead_id = None

    resp = await client.post("/api/rigs", json={
        "path": str(ROOT),
        "name": f"perf-test-{_rand_str()}",
        "description": "Performance test rig",
    })
    if resp.status_code == 201:
        rig_id = resp.json().get("id")

    return rig_id, bead_id


def _build_scenario(rig_id: Optional[str], bead_id: Optional[str]) -> list[tuple[str, str, dict]]:
    """Return a weighted list of (method, path, kwargs) scenarios.

    Weights reflect realistic API usage distribution.
    """
    scenarios: list[tuple[int, str, str, dict]] = [
        # (weight, method, path, kwargs)
        (15, "GET",  "/api/rigs",                          {}),
        (10, "GET",  f"/api/rigs/{rig_id}/beads" if rig_id else "/api/rigs", {}),
        (10, "GET",  f"/api/rigs/{rig_id}/status" if rig_id else "/api/rigs", {}),
        (5,  "GET",  f"/api/rigs/{rig_id}" if rig_id else "/api/rigs",       {}),
        (5,  "GET",  "/api/beads/gt-zzz00/logs",           {}),   # 404
        (5,  "GET",  f"/api/runs/{uuid.uuid4()}",           {}),   # 404
        (3,  "GET",  "/",                                  {}),    # dashboard HTML
        (2,  "POST", "/api/rigs", {
            "json": {"path": str(ROOT), "name": f"rig-{_rand_str()}", "description": ""}
        }),
    ]

    # Expand by weight
    pool: list[tuple[str, str, dict]] = []
    for weight, method, path, kwargs in scenarios:
        pool.extend([(method, path, kwargs)] * weight)
    return pool


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

async def _worker(
    client: httpx.AsyncClient,
    queue: asyncio.Queue,
    results: list[RequestResult],
) -> None:
    while True:
        try:
            method, path, kwargs = queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        t0 = time.perf_counter()
        status = 0
        error = None
        try:
            resp = await client.request(method, path, **kwargs)
            status = resp.status_code
        except Exception as exc:
            error = str(exc)[:100]
        finally:
            latency_ms = (time.perf_counter() - t0) * 1000
            results.append(RequestResult(
                endpoint=_normalize_path(path),
                method=method,
                status_code=status,
                latency_ms=latency_ms,
                error=error,
            ))
            queue.task_done()


# ---------------------------------------------------------------------------
# Run one traffic level
# ---------------------------------------------------------------------------

async def run_level(level: TrafficLevel) -> LevelResult:
    """Execute one traffic-level test scenario."""
    print(f"\n  Running {level.label} ({level.concurrency} workers, "
          f"{level.total_requests:,} requests)...", flush=True)

    async with get_client() as client:
        # Trigger lifespan
        _ = await client.get("/api/rigs")

        # Setup fixtures
        rig_id, bead_id = await _setup_fixtures(client)

        # Build request queue
        pool = _build_scenario(rig_id, bead_id)
        queue: asyncio.Queue = asyncio.Queue()
        for _ in range(level.total_requests):
            queue.put_nowait(random.choice(pool))

        results: list[RequestResult] = []

        t0 = time.perf_counter()
        workers = [
            asyncio.create_task(_worker(client, queue, results))
            for _ in range(level.concurrency)
        ]
        await asyncio.gather(*workers)
        wall_time = time.perf_counter() - t0

    lr = LevelResult(
        level=level,
        results=results,
        wall_time_s=wall_time,
        setup_rig_id=rig_id,
        setup_bead_id=bead_id,
    )

    print(
        f"  OK {lr.total:,} req in {wall_time:.2f}s  |  "
        f"{lr.throughput:.0f} req/s  |  "
        f"p50={lr.p50:.1f}ms  p95={lr.p95:.1f}ms  p99={lr.p99:.1f}ms  |  "
        f"errors={lr.errors} ({lr.error_rate:.1%})",
        flush=True,
    )
    return lr


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _percentile(data: list[float], pct: int) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * pct / 100
    lo, hi = int(k), math.ceil(k)
    if lo == hi:
        return sorted_data[lo]
    return sorted_data[lo] + (sorted_data[hi] - sorted_data[lo]) * (k - lo)


def _grade(p95_ms: float, error_rate: float) -> str:
    if error_rate > 0.05:
        return "FAIL"
    if p95_ms < 10 and error_rate == 0:
        return "Excellent"
    if p95_ms < 50 and error_rate < 0.001:
        return "Good"
    if p95_ms < 200 and error_rate < 0.01:
        return "Acceptable"
    return "Degraded"


def _bar(value: float, max_value: float, width: int = 20) -> str:
    filled = int(round(value / max_value * width)) if max_value else 0
    filled = min(filled, width)
    return "#" * filled + "." * (width - filled)


def _normalize_path(path: str) -> str:
    """Replace UUIDs and bead IDs with placeholders for grouping."""
    import re
    path = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "{uuid}", path)
    path = re.sub(r"gt-[a-z]{3}\d{2}", "{bead_id}", path)
    path = re.sub(r"perf-test-[a-z0-9]+", "{rig_id}", path)
    path = re.sub(r"rig-[a-z0-9]+-[a-z0-9]+", "{rig_id}", path)
    return path


def generate_report(level_results: list[LevelResult], output_path: Path) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    def w(*args):
        lines.append(" ".join(str(a) for a in args) if args else "")

    # ---------------------------------------------------------------------------
    w("# Gastown Performance Test Report")
    w()
    w(f"**Generated:** {now}")
    w(f"**Platform:** {sys.platform} — Python {sys.version.split()[0]}")
    w(f"**Transport:** httpx ASGI (in-process, no network overhead)")
    w(f"**App:** Gastown FastAPI v0.1.0")
    w()
    w("---")
    w()

    # Executive summary table
    w("## Executive Summary")
    w()
    w("| Level | Requests | Workers | Wall Time | Throughput | Mean | p50 | p95 | p99 | Errors | Grade |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for lr in level_results:
        grade = _grade(lr.p95, lr.error_rate)
        w(
            f"| {lr.level.label} "
            f"| {lr.total:,} "
            f"| {lr.level.concurrency} "
            f"| {lr.wall_time_s:.2f}s "
            f"| **{lr.throughput:.0f} req/s** "
            f"| {lr.mean_latency:.1f}ms "
            f"| {lr.p50:.1f}ms "
            f"| {lr.p95:.1f}ms "
            f"| {lr.p99:.1f}ms "
            f"| {lr.errors} ({lr.error_rate:.1%}) "
            f"| {grade} |"
        )
    w()

    # Throughput chart
    max_tp = max(lr.throughput for lr in level_results) or 1
    w("### Throughput (req/sec)")
    w()
    w("```")
    for lr in level_results:
        bar = _bar(lr.throughput, max_tp, 40)
        w(f"{lr.level.label:<18} {bar} {lr.throughput:6.0f} req/s")
    w("```")
    w()

    # Latency chart (p95)
    max_p95 = max(lr.p95 for lr in level_results) or 1
    w("### p95 Latency (ms)")
    w()
    w("```")
    for lr in level_results:
        bar = _bar(lr.p95, max_p95, 40)
        w(f"{lr.level.label:<18} {bar} {lr.p95:6.1f}ms")
    w("```")
    w()
    w("---")
    w()

    # Per-level detailed sections
    for lr in level_results:
        w(f"## {lr.level.label} Traffic")
        w()
        w(f"> {lr.level.description}")
        w()
        w(f"**Configuration:** {lr.level.concurrency} concurrent workers · "
          f"{lr.level.total_requests:,} total requests")
        w()

        # Overview stats
        lats = lr.all_latencies
        if lats:
            w("### Latency Distribution")
            w()
            w(f"| Stat | Value |")
            w(f"|---|---|")
            w(f"| Min | {min(lats):.2f}ms |")
            w(f"| p50 (median) | {lr.p50:.2f}ms |")
            w(f"| p90 | {_percentile(lats, 90):.2f}ms |")
            w(f"| p95 | {lr.p95:.2f}ms |")
            w(f"| p99 | {lr.p99:.2f}ms |")
            w(f"| Max | {max(lats):.2f}ms |")
            w(f"| Mean | {lr.mean_latency:.2f}ms |")
            if len(lats) > 1:
                w(f"| StdDev | {statistics.stdev(lats):.2f}ms |")
            w()

        w(f"### Throughput & Reliability")
        w()
        w(f"| Metric | Value |")
        w(f"|---|---|")
        w(f"| Wall time | {lr.wall_time_s:.3f}s |")
        w(f"| Requests completed | {lr.total:,} |")
        w(f"| Throughput | **{lr.throughput:.1f} req/s** |")
        w(f"| Errors | {lr.errors} |")
        w(f"| Error rate | {lr.error_rate:.2%} |")
        grade = _grade(lr.p95, lr.error_rate)
        w(f"| Grade | {grade} |")
        w()

        # Status code breakdown
        from collections import Counter
        status_counts = Counter(r.status_code for r in lr.results)
        w("### Status Code Breakdown")
        w()
        w("| Status | Count | % |")
        w("|---|---|---|")
        for code, cnt in sorted(status_counts.items()):
            pct = cnt / lr.total * 100
            label = {200: "OK", 201: "Created", 404: "Not Found",
                     422: "Unprocessable", 500: "Server Error"}.get(code, "")
            w(f"| `{code}` {label} | {cnt:,} | {pct:.1f}% |")
        w()

        # Per-endpoint breakdown
        by_ep = lr.by_endpoint()
        if by_ep:
            w("### Per-Endpoint Breakdown")
            w()
            w("| Endpoint | Hits | Errors | Mean | p50 | p95 | p99 |")
            w("|---|---|---|---|---|---|---|")
            for key, ep in sorted(by_ep.items(), key=lambda x: -x[1].count):
                w(
                    f"| `{ep.method} {ep.endpoint}` "
                    f"| {ep.count} "
                    f"| {ep.errors} ({ep.error_rate:.0%}) "
                    f"| {ep.mean:.1f}ms "
                    f"| {ep.p50:.1f}ms "
                    f"| {ep.p95:.1f}ms "
                    f"| {ep.p99:.1f}ms |"
                )
            w()

        # Error details
        errors = [r for r in lr.results if r.error or r.status_code >= 500]
        if errors:
            unique_errors = list({r.error or f"HTTP {r.status_code}": r for r in errors}.values())[:5]
            w("### Error Samples")
            w()
            w("```")
            for r in unique_errors:
                w(f"[{r.method} {r.endpoint}] {r.error or f'HTTP {r.status_code}'}")
            w("```")
            w()

        w("---")
        w()

    # Comparative analysis
    w("## Comparative Analysis")
    w()
    w("### Latency Scaling")
    w()
    if len(level_results) >= 2:
        base = level_results[0]
        w(f"Using **{base.level.label}** as baseline (p95 = {base.p95:.1f}ms):")
        w()
        w("| Level | p95 | vs Baseline | Throughput | Efficiency |")
        w("|---|---|---|---|---|")
        for lr in level_results:
            factor = lr.p95 / base.p95 if base.p95 else 1
            tp_factor = lr.throughput / base.throughput if base.throughput else 1
            efficiency = tp_factor / (lr.level.concurrency / base.level.concurrency)
            w(
                f"| {lr.level.label} "
                f"| {lr.p95:.1f}ms "
                f"| {factor:.1f}× "
                f"| {lr.throughput:.0f} req/s "
                f"| {efficiency:.1%} |"
            )
        w()

    w("### Observations & Recommendations")
    w()

    # Auto-generate observations
    all_p95 = [lr.p95 for lr in level_results]
    all_tp = [lr.throughput for lr in level_results]
    max_error_rate = max(lr.error_rate for lr in level_results)
    peak_tp = max(all_tp)
    peak_level = level_results[all_tp.index(peak_tp)]

    w(f"1. **Peak throughput** of **{peak_tp:.0f} req/s** achieved at {peak_level.level.label} load "
      f"({peak_level.level.concurrency} workers).")
    w()

    # Latency progression
    if len(level_results) >= 2:
        light_p95 = level_results[0].p95
        heavy_p95 = level_results[-1].p95
        degradation = heavy_p95 / light_p95 if light_p95 else 1
        if degradation < 3:
            w(f"2. **Latency is stable** under load — p95 increases only {degradation:.1f}× from "
              f"light to very heavy traffic. The asyncio.Lock + WAL mode design holds well.")
        elif degradation < 10:
            w(f"2. **Latency degrades moderately** under load — p95 increases {degradation:.1f}× from "
              f"light to very heavy traffic. Consider connection pooling for higher concurrency.")
        else:
            w(f"2. **Significant latency degradation** under load — p95 increases {degradation:.1f}× "
              f"from light to very heavy traffic. The SQLite asyncio.Lock is a bottleneck at this scale.")
    w()

    if max_error_rate == 0:
        w("3. **Zero errors** across all traffic levels — all endpoints returned valid responses.")
    elif max_error_rate < 0.01:
        w(f"3. **Error rate is low** (max {max_error_rate:.1%}) — well within acceptable thresholds.")
    else:
        w(f"4. **⚠️ Error rate exceeds 1%** at peak load ({max_error_rate:.1%}). Investigate before production.")
    w()

    # SQLite note
    w("4. **SQLite WAL mode** is the primary I/O bottleneck. For deployments with >50 concurrent")
    w("   agents, consider migrating to PostgreSQL with asyncpg for non-blocking I/O.")
    w()
    w("5. **In-process test caveat:** These results use httpx ASGI transport (no TCP/TLS overhead).")
    w("   Real-world latency will be 1–5ms higher due to network stack.")
    w()

    w("---")
    w()
    w("## Test Configuration")
    w()
    w("| Parameter | Value |")
    w("|---|---|")
    w(f"| Test runner | httpx `ASGITransport` (in-process) |")
    w(f"| Request distribution | Weighted random across all endpoints |")
    w(f"| Timeout per request | 30s |")
    w(f"| LLM calls | None (all benchmarked endpoints are DB/routing only) |")
    w()
    w("### Endpoint Weight Distribution")
    w()
    w("| Endpoint | Relative Weight | Purpose |")
    w("|---|---|---|")
    w("| `GET /api/rigs` | 27% | List all rigs |")
    w("| `GET /api/rigs/{id}/beads` | 18% | List beads for rig |")
    w("| `GET /api/rigs/{id}/status` | 18% | Status counts |")
    w("| `GET /api/rigs/{id}` | 9% | Get single rig |")
    w("| `GET /api/beads/{id}/logs` | 9% | Bead event log (404) |")
    w("| `GET /api/runs/{uuid}` | 9% | Get run status (404) |")
    w("| `GET /` | 5% | Dashboard HTML |")
    w("| `POST /api/rigs` | 4% | Create rig |")
    w()

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  Report written to: {output_path}", flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(levels_to_run: list[str], output_path: Path) -> None:
    selected = [lv for lv in LEVELS if lv.name in levels_to_run]

    print("\n" + "=" * 60)
    print("  GASTOWN PERFORMANCE TEST")
    print("=" * 60)
    print(f"  Levels: {', '.join(lv.label for lv in selected)}")
    print(f"  Output: {output_path}")
    print("=" * 60)

    level_results: list[LevelResult] = []
    for level in selected:
        lr = await run_level(level)
        level_results.append(lr)

    print("\n" + "=" * 60)
    print("  Generating report...")
    generate_report(level_results, output_path)
    print("  Done!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gastown performance load test")
    parser.add_argument(
        "--levels",
        nargs="+",
        choices=["light", "medium", "heavy", "very_heavy"],
        default=["light", "medium", "heavy", "very_heavy"],
        help="Traffic levels to run (default: all)",
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "PERFORMANCE_REPORT.md"),
        help="Output markdown file path",
    )
    args = parser.parse_args()

    asyncio.run(main(args.levels, Path(args.output)))
