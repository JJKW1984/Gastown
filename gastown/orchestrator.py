"""GastownOrchestrator — the shared async engine for CLI and web.

Both gastown.cli and gastown.web.app delegate to this class.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from gastown.agents.mayor import Mayor
from gastown.agents.polecat import PoleCAT
from gastown.agents.refinery import Refinery
from gastown.agents.witness import Witness
from gastown.models import Bead, BeadStatus, Rig, RunResult, WitnessEvent
from gastown.storage import GastownDB
from gastown.tools.gt_tools import (
    gt_ensure_initial_commit,
    gt_setup_worktree,
    gt_teardown_worktree,
)

ProgressCallback = Callable[[WitnessEvent], Awaitable[None]]


class GastownOrchestrator:
    """Runs the full Mayor → PoleCAT → Witness → Refinery pipeline.

    Parameters
    ----------
    db:
        Initialized GastownDB instance.
    max_concurrent:
        Maximum number of PoleCATs running simultaneously.
    stuck_timeout:
        Seconds of inactivity before Witness nudges a PoleCAT.
    """

    def __init__(
        self,
        db: GastownDB,
        max_concurrent: int = 4,
        stuck_timeout: int = 120,
    ) -> None:
        self.db = db
        self.max_concurrent = max_concurrent
        self.stuck_timeout = stuck_timeout

    async def run(
        self,
        goal: str,
        rig: Rig,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> RunResult:
        """Execute a full Gastown run for the given goal on the given rig.

        Emits WitnessEvent objects to progress_callback (if provided) in real-time.
        Returns a RunResult with all merged/rejected bead IDs and the Mayor's review.
        """
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        max_concurrent = int(
            os.getenv("GASTOWN_MAX_CONCURRENT_POLECATS", str(self.max_concurrent))
        )
        stuck_timeout = int(
            os.getenv("GASTOWN_STUCK_TIMEOUT_SECONDS", str(self.stuck_timeout))
        )

        # 1. Ensure the repo has at least one commit (git worktrees require it)
        await gt_ensure_initial_commit(rig.repo_path)

        # 2. Mayor decomposes the goal into beads
        mayor = Mayor(db=self.db, rig=rig)
        beads = await mayor.decompose(goal, rig)

        if not beads:
            return RunResult(
                run_id=run_id,
                rig_id=rig.id,
                goal=goal,
                beads=[],
                mayor_review="Mayor produced no beads for this goal.",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )

        # 3. Create convoy and mark beads in_progress
        await mayor.sling(beads, rig)

        # 4. Setup git worktrees for each bead
        for bead in beads:
            try:
                worktree_path = await gt_setup_worktree(bead, rig)
                await self.db.update_bead_status(
                    bead.id,
                    BeadStatus.IN_PROGRESS,
                    worktree_path=worktree_path,
                    branch_name=f"bead/{bead.id}",
                )
                bead.worktree_path = worktree_path
                bead.branch_name = f"bead/{bead.id}"
            except Exception as exc:
                await self.db.update_bead_status(bead.id, BeadStatus.FAILED)
                await self.db.log_event(
                    "worktree_error",
                    str(exc)[:300],
                    bead_id=bead.id,
                )

        # Reload beads to get updated worktree_path / branch_name
        beads = [b for b in await self.db.list_beads(rig.id) if b.status == BeadStatus.IN_PROGRESS]

        # 5. Set up shared queues
        event_queue: asyncio.Queue[WitnessEvent] = asyncio.Queue()
        nudge_queues: dict[str, asyncio.Queue] = {b.id: asyncio.Queue() for b in beads}

        # 6. Build and wrap the progress_callback into the event_queue consumer
        async def _drain_events() -> None:
            while True:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.2)
                    if progress_callback:
                        try:
                            await progress_callback(event)
                        except Exception:
                            pass
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    # Drain remaining events
                    while not event_queue.empty():
                        event = event_queue.get_nowait()
                        if progress_callback:
                            try:
                                await progress_callback(event)
                            except Exception:
                                pass
                    return

        drain_task = asyncio.create_task(_drain_events())

        # 7. Run PoleCATs concurrently (bounded by semaphore)
        semaphore = asyncio.Semaphore(max_concurrent)
        active_tasks: dict[str, asyncio.Task] = {}

        async def _run_polecat(bead: Bead) -> dict:
            async with semaphore:
                polecat = PoleCAT(db=self.db, rig=rig)
                return await polecat.execute(
                    bead,
                    event_queue=event_queue,
                    nudge_queue=nudge_queues.get(bead.id),
                )

        for bead in beads:
            task = asyncio.create_task(_run_polecat(bead))
            active_tasks[bead.id] = task

        # 8. Run Witness concurrently
        witness = Witness(db=self.db, rig=rig, stuck_timeout=stuck_timeout)
        witness_task = asyncio.create_task(
            witness.monitor(event_queue, active_tasks, nudge_queues)
        )

        # 9. Wait for all PoleCATs to finish
        if active_tasks:
            await asyncio.gather(*active_tasks.values(), return_exceptions=True)

        witness_task.cancel()
        try:
            await witness_task
        except asyncio.CancelledError:
            pass

        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass

        # 10. Refinery: merge completed bead branches
        completed_beads = await self.db.list_beads(rig.id, status=BeadStatus.DONE)
        refinery = Refinery(rig=rig, db=self.db)
        refinery_result = await refinery.process_completed_beads(completed_beads)

        # 11. Teardown worktrees
        for bead in beads:
            try:
                await gt_teardown_worktree(bead, rig)
            except Exception:
                pass

        # 12. Mayor reviews the completed work
        merged_beads = [b for b in completed_beads if b.id in refinery_result.merged]
        mayor_review = await mayor.review_results(merged_beads)

        # Save done_summary into bead metadata for the review
        for bead in merged_beads:
            await self.db.update_bead_status(bead.id, BeadStatus.MERGED)

        # 13. Build and return RunResult
        all_beads = await self.db.list_beads(rig.id)
        return RunResult(
            run_id=run_id,
            rig_id=rig.id,
            goal=goal,
            beads=all_beads,
            merged=refinery_result.merged,
            rejected=refinery_result.rejected,
            mayor_review=mayor_review,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )
