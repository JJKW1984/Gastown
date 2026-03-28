"""Unit tests for gastown.models."""

import re
import pytest
from gastown.models import (
    Bead, BeadStatus, Convoy, DecompositionResult,
    Rig, RunResult, WitnessEvent, gen_bead_id, gen_convoy_id,
)


class TestGenBeadId:
    def test_format(self):
        bead_id = gen_bead_id()
        assert re.match(r"^gt-[a-z]{3}\d{2}$", bead_id), f"Invalid format: {bead_id}"

    def test_uniqueness(self):
        ids = {gen_bead_id() for _ in range(1000)}
        # With 26^3 * 10^2 = ~1.7M combos, 1000 should be mostly unique
        assert len(ids) > 990


class TestGenConvoyId:
    def test_format(self):
        cid = gen_convoy_id()
        assert cid.startswith("convoy-")
        assert len(cid) == 15  # convoy- (7) + 8 hex chars


class TestBead:
    def test_defaults(self, sample_rig):
        bead = Bead(rig_id=sample_rig.id, title="t", description="d")
        assert bead.status == BeadStatus.PENDING
        assert bead.priority == 0
        assert bead.metadata == {}
        assert re.match(r"^gt-", bead.id)

    def test_explicit_id(self, sample_rig):
        bead = Bead(id="gt-xyz99", rig_id=sample_rig.id, title="t", description="d")
        assert bead.id == "gt-xyz99"

    def test_status_enum(self):
        assert BeadStatus.PENDING.value == "pending"
        assert BeadStatus.IN_PROGRESS.value == "in_progress"
        assert BeadStatus.DONE.value == "done"
        assert BeadStatus.FAILED.value == "failed"
        assert BeadStatus.MERGED.value == "merged"
        assert BeadStatus.REJECTED.value == "rejected"


class TestDecompositionResult:
    def test_validation(self):
        data = {
            "beads": [
                {"title": "Do X", "description": "Do X in detail", "priority": 0,
                 "estimated_files": ["x.py"], "depends_on": []}
            ],
            "summary": "Break into 1 bead",
        }
        result = DecompositionResult.model_validate(data)
        assert len(result.beads) == 1
        assert result.beads[0].title == "Do X"
        assert result.summary == "Break into 1 bead"

    def test_empty_beads(self):
        result = DecompositionResult(beads=[], summary="nothing to do")
        assert result.beads == []


class TestWitnessEvent:
    def test_fields(self):
        ev = WitnessEvent(polecat_id="p1", bead_id="gt-abc01", event_type="heartbeat")
        assert ev.event_type == "heartbeat"
        assert ev.details == ""
        assert ev.timestamp is not None
