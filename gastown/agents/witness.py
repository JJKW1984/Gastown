"""Witness — per-rig lifecycle monitor for PoleCAT agents.

Watches the shared event queue, detects stuck agents, and issues nudges.
No LLM calls — pure asyncio logic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from gastown.models import BeadStatus, Rig, WitnessEvent
from gastown.storage import GastownDB

STUCK_TIMEOUT_DEFAULT = 120  # seconds without a heartbeat = stuck
MAX_NUDGES = 3
POLL_INTERVAL = 10.0  # seconds between stuck-checks when queue is idle


class Witness:
    """Monitors the shared WitnessEvent queue and manages PoleCAT health.

    Usage:
        witness = Witness(db, rig, stuck_timeout=120)
        await witness.monitor(event_queue, active_tasks, nudge_queues)
    """

    def __init__(
        self,
        db: GastownDB,
        rig: Rig,
        stuck_timeout: int = STUCK_TIMEOUT_DEFAULT,
    ) -> None:
        self.db = db
        self.rig = rig
        self.stuck_timeout = stuck_timeout
        self._last_heartbeat: dict[str, datetime] = {}
        self._nudge_counts: dict[str, int] = {}

    async def monitor(
        self,
        event_queue: asyncio.Queue,
        active_tasks: dict[str, asyncio.Task],
        nudge_queues: dict[str, asyncio.Queue],
    ) -> None:
        """Run until all tasks in active_tasks are done.

        Parameters
        ----------
        event_queue:
            Shared queue receiving WitnessEvent objects from polecats.
        active_tasks:
            Mapping of bead_id → asyncio.Task. Tasks are removed as they complete.
        nudge_queues:
            Mapping of bead_id → per-polecat asyncio.Queue for nudge signals.
        """
        while active_tasks:
            try:
                event: WitnessEvent = await asyncio.wait_for(
                    event_queue.get(), timeout=POLL_INTERVAL
                )
                await self._process_event(event, active_tasks, nudge_queues)
            except asyncio.TimeoutError:
                await self._check_for_stuck(active_tasks, nudge_queues)
            except asyncio.CancelledError:
                return

    async def _process_event(
        self,
        event: WitnessEvent,
        active_tasks: dict[str, asyncio.Task],
        nudge_queues: dict[str, asyncio.Queue],
    ) -> None:
        bead_id = event.bead_id

        if event.event_type == "heartbeat":
            self._last_heartbeat[bead_id] = datetime.now(timezone.utc)
            self._nudge_counts.setdefault(bead_id, 0)

        elif event.event_type == "done":
            active_tasks.pop(bead_id, None)
            self._last_heartbeat.pop(bead_id, None)
            self._nudge_counts.pop(bead_id, None)
            await self.db.log_event(
                "witness_done",
                f"Witnessed bead {bead_id} done: {event.details[:100]}",
                bead_id=bead_id,
            )

        elif event.event_type == "failed":
            active_tasks.pop(bead_id, None)
            self._last_heartbeat.pop(bead_id, None)
            self._nudge_counts.pop(bead_id, None)
            await self.db.log_event(
                "witness_failed",
                f"Witnessed bead {bead_id} failed: {event.details[:200]}",
                bead_id=bead_id,
            )

    async def _check_for_stuck(
        self,
        active_tasks: dict[str, asyncio.Task],
        nudge_queues: dict[str, asyncio.Queue],
    ) -> None:
        """Scan all active tasks for stuck polecats."""
        now = datetime.now(timezone.utc)
        to_cancel: list[str] = []

        for bead_id, task in list(active_tasks.items()):
            if task.done():
                active_tasks.pop(bead_id, None)
                continue

            last = self._last_heartbeat.get(bead_id)
            if last is None:
                self._last_heartbeat[bead_id] = now
                continue

            elapsed = (now - last).total_seconds()
            if elapsed < self.stuck_timeout:
                continue

            # Polecat is stuck
            nudge_count = self._nudge_counts.get(bead_id, 0)
            if nudge_count < MAX_NUDGES:
                self._nudge_counts[bead_id] = nudge_count + 1
                self._last_heartbeat[bead_id] = now  # reset timer after nudge
                await self.db.log_event(
                    "witness_nudge",
                    f"Nudging stuck polecat for bead {bead_id} "
                    f"(nudge {nudge_count + 1}/{MAX_NUDGES}, idle {elapsed:.0f}s)",
                    bead_id=bead_id,
                )
                if bead_id in nudge_queues:
                    await nudge_queues[bead_id].put("nudge")
            else:
                # Max nudges exceeded — cancel the task
                to_cancel.append(bead_id)

        for bead_id in to_cancel:
            task = active_tasks.pop(bead_id, None)
            if task and not task.done():
                task.cancel()
            await self.db.update_bead_status(bead_id, BeadStatus.FAILED)
            await self.db.log_event(
                "witness_cancel",
                f"Cancelled stuck polecat for bead {bead_id} after {MAX_NUDGES} nudges",
                bead_id=bead_id,
            )
