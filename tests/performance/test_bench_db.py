"""Performance benchmarks for GastownDB operations.

Run with: pytest tests/performance/ --benchmark-only
Or:        pytest tests/performance/ --benchmark-compare
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gastown.models import Bead, BeadStatus, Rig
from gastown.storage import GastownDB


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sync_db(tmp_path: Path) -> GastownDB:
    """Synchronous setup for benchmark fixtures."""
    db = GastownDB(str(tmp_path / "bench.db"))
    asyncio.get_event_loop().run_until_complete(db.initialize())
    return db


@pytest.fixture
def bench_rig() -> Rig:
    return Rig(id="bench-rig", name="Benchmark Rig", repo_path="/tmp/bench")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestDBBenchmarks:
    def test_bead_create_throughput(self, benchmark, sync_db, bench_rig):
        """Benchmark: how fast can we create beads?"""
        _run(sync_db.create_rig(bench_rig))
        counter = [0]

        def create_one():
            counter[0] += 1
            bead = Bead(
                rig_id=bench_rig.id,
                title=f"Bead {counter[0]}",
                description="benchmark bead",
            )
            _run(sync_db.create_bead(bead))

        benchmark(create_one)

    def test_bead_list_100(self, benchmark, sync_db, bench_rig):
        """Benchmark: list 100 beads."""
        _run(sync_db.create_rig(bench_rig))
        for i in range(100):
            bead = Bead(rig_id=bench_rig.id, title=f"Bead {i}", description="d")
            _run(sync_db.create_bead(bead))

        benchmark(lambda: _run(sync_db.list_beads(bench_rig.id)))

    def test_status_update_throughput(self, benchmark, sync_db, bench_rig):
        """Benchmark: status updates (hot path during polecat execution)."""
        _run(sync_db.create_rig(bench_rig))
        bead = Bead(rig_id=bench_rig.id, title="bench bead", description="d")
        _run(sync_db.create_bead(bead))

        statuses = [BeadStatus.IN_PROGRESS, BeadStatus.DONE, BeadStatus.MERGED]
        idx = [0]

        def update_one():
            st = statuses[idx[0] % len(statuses)]
            idx[0] += 1
            _run(sync_db.update_bead_status(bead.id, st))

        benchmark(update_one)

    def test_event_log_throughput(self, benchmark, sync_db, bench_rig):
        """Benchmark: logging events (called on every heartbeat)."""
        _run(sync_db.create_rig(bench_rig))
        bead = Bead(rig_id=bench_rig.id, title="b", description="d")
        _run(sync_db.create_bead(bead))

        benchmark(lambda: _run(sync_db.log_event("heartbeat", "tick", bead_id=bead.id)))
