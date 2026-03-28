"""Performance benchmarks for model serialization (hot path in web API)."""

from __future__ import annotations

import json

import pytest

from gastown.models import Bead, BeadStatus, DecompositionResult, RunResult, Rig, WitnessEvent


class TestSerializationBenchmarks:
    def test_bead_serialize(self, benchmark):
        bead = Bead(rig_id="r", title="t", description="d")
        benchmark(lambda: bead.model_dump(mode="json"))

    def test_bead_deserialize(self, benchmark):
        data = {"id": "gt-abc01", "rig_id": "r", "title": "t", "description": "d",
                "status": "pending", "priority": 0, "convoy_id": None,
                "polecat_id": None, "branch_name": None, "worktree_path": None,
                "created_at": "2025-01-01T00:00:00+00:00",
                "updated_at": "2025-01-01T00:00:00+00:00", "metadata": {}}
        benchmark(lambda: Bead.model_validate(data))

    def test_decomposition_result_parse(self, benchmark):
        data = {
            "beads": [
                {"title": f"Bead {i}", "description": f"Do thing {i}",
                 "priority": i, "estimated_files": [f"file{i}.py"], "depends_on": []}
                for i in range(10)
            ],
            "summary": "10 beads decomposed",
        }
        benchmark(lambda: DecompositionResult.model_validate(data))

    def test_witness_event_serialize(self, benchmark):
        ev = WitnessEvent(polecat_id="p1", bead_id="gt-abc01", event_type="heartbeat",
                          details="tick")
        benchmark(lambda: ev.model_dump(mode="json"))

    def test_witness_event_json_dumps(self, benchmark):
        """JSON serialization used in WebSocket send."""
        ev = WitnessEvent(polecat_id="p1", bead_id="gt-abc01", event_type="heartbeat")
        benchmark(lambda: json.dumps(ev.model_dump(mode="json")))
