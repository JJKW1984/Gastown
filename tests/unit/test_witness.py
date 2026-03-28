"""Unit tests for the Witness monitor."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from gastown.agents.witness import Witness
from gastown.models import BeadStatus, WitnessEvent


class TestWitnessProcessEvent:
    async def test_heartbeat_updates_timestamp(self, db, sample_rig):
        witness = Witness(db=db, rig=sample_rig, stuck_timeout=120)
        active = {"gt-abc01": asyncio.create_task(asyncio.sleep(10))}
        nudge_queues = {"gt-abc01": asyncio.Queue()}

        event = WitnessEvent(polecat_id="p1", bead_id="gt-abc01", event_type="heartbeat")
        await witness._process_event(event, active, nudge_queues)

        assert "gt-abc01" in witness._last_heartbeat
        # Cleanup
        active["gt-abc01"].cancel()

    async def test_done_removes_from_active(self, db, sample_rig):
        witness = Witness(db=db, rig=sample_rig, stuck_timeout=120)
        task = asyncio.create_task(asyncio.sleep(0))
        await task  # let it complete
        active = {"gt-abc01": task}
        nudge_queues = {}

        event = WitnessEvent(polecat_id="p1", bead_id="gt-abc01", event_type="done", details="ok")
        await witness._process_event(event, active, nudge_queues)

        assert "gt-abc01" not in active

    async def test_failed_removes_from_active(self, db, sample_rig):
        witness = Witness(db=db, rig=sample_rig, stuck_timeout=120)
        task = asyncio.create_task(asyncio.sleep(0))
        await task
        active = {"gt-abc01": task}
        nudge_queues = {}

        event = WitnessEvent(polecat_id="p1", bead_id="gt-abc01", event_type="failed")
        await witness._process_event(event, active, nudge_queues)

        assert "gt-abc01" not in active


class TestWitnessStuckDetection:
    async def test_nudges_stuck_polecat(self, db, sample_rig):
        witness = Witness(db=db, rig=sample_rig, stuck_timeout=5)
        # Set last heartbeat to 10 seconds ago
        old_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        witness._last_heartbeat["gt-abc01"] = old_time
        witness._nudge_counts["gt-abc01"] = 0

        task = asyncio.create_task(asyncio.sleep(60))
        active = {"gt-abc01": task}
        nudge_queue = asyncio.Queue()
        nudge_queues = {"gt-abc01": nudge_queue}

        await witness._check_for_stuck(active, nudge_queues)

        assert witness._nudge_counts["gt-abc01"] == 1
        assert not nudge_queue.empty()
        task.cancel()

    async def test_cancels_after_max_nudges(self, db, sample_rig):
        from gastown.agents.witness import MAX_NUDGES
        witness = Witness(db=db, rig=sample_rig, stuck_timeout=5)
        old_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        witness._last_heartbeat["gt-abc01"] = old_time
        witness._nudge_counts["gt-abc01"] = MAX_NUDGES  # already at max

        # Create a Bead for the DB so update_bead_status can find it
        from gastown.models import Bead
        bead = Bead(id="gt-abc01", rig_id=sample_rig.id, title="t", description="d")
        await db.create_rig(sample_rig)
        await db.create_bead(bead)

        task = asyncio.create_task(asyncio.sleep(60))
        active = {"gt-abc01": task}
        nudge_queues: dict = {}

        await witness._check_for_stuck(active, nudge_queues)
        # Give the event loop a turn to process the cancellation
        await asyncio.sleep(0)

        assert "gt-abc01" not in active
        # Task should be cancelled or in the process of cancelling
        assert task.cancelled() or task.cancelling() > 0

    async def test_no_nudge_within_timeout(self, db, sample_rig):
        witness = Witness(db=db, rig=sample_rig, stuck_timeout=120)
        witness._last_heartbeat["gt-abc01"] = datetime.now(timezone.utc)  # just now
        witness._nudge_counts["gt-abc01"] = 0

        task = asyncio.create_task(asyncio.sleep(0))
        active = {"gt-abc01": task}
        nudge_queues = {"gt-abc01": asyncio.Queue()}

        await witness._check_for_stuck(active, nudge_queues)

        # Should NOT have nudged
        assert witness._nudge_counts["gt-abc01"] == 0
        assert nudge_queues["gt-abc01"].empty()
        task.cancel()
