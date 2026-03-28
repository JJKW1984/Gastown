"""Refinery — Bors-style bisecting merge queue processor.

Merges completed bead branches into the main branch one by one (or in batches
with bisect-on-failure). No LLM calls — pure git subprocess logic.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Optional

from gastown.models import Bead, BeadStatus, Rig
from gastown.storage import GastownDB


@dataclasses.dataclass
class RefineryResult:
    merged: list[str] = dataclasses.field(default_factory=list)
    rejected: list[str] = dataclasses.field(default_factory=list)
    conflicts: dict[str, str] = dataclasses.field(default_factory=dict)


class Refinery:
    """Bors-style bisecting merge queue for completed bead branches.

    Algorithm:
    1. Sort beads by priority
    2. Try to merge all as a batch
    3. If conflict: bisect → retry first half, re-queue second half
    4. If single bead conflicts: reject it
    """

    def __init__(self, rig: Rig, db: GastownDB) -> None:
        self.rig = rig
        self.db = db

    async def process_completed_beads(self, beads: list[Bead]) -> RefineryResult:
        """Merge completed bead branches using bisecting queue logic."""
        result = RefineryResult()

        # Sort by priority (lower = higher priority)
        queue = sorted(beads, key=lambda b: b.priority)

        # Get the current main branch name
        main_branch = await self._get_main_branch()

        while queue:
            batch = queue[:]
            queue.clear()

            success = await self._try_merge_batch(batch, main_branch, result)
            if not success:
                if len(batch) == 1:
                    # Single bead failed — reject it
                    bead = batch[0]
                    result.rejected.append(bead.id)
                    await self.db.update_bead_status(bead.id, BeadStatus.REJECTED)
                    await self.db.log_event(
                        "refinery_reject",
                        f"Bead {bead.id} rejected due to merge conflict",
                        bead_id=bead.id,
                    )
                else:
                    # Bisect: retry first half, re-queue second half
                    mid = len(batch) // 2
                    first_half = batch[:mid]
                    second_half = batch[mid:]
                    # First half goes back to front of processing
                    queue = first_half + second_half + queue

        return result

    async def _try_merge_batch(
        self,
        batch: list[Bead],
        main_branch: str,
        result: RefineryResult,
    ) -> bool:
        """Attempt to merge all beads in the batch sequentially.

        Returns True if all merged cleanly, False if any conflict occurred.
        On conflict, all partial merges are rolled back via git reset.
        """
        merged_in_batch: list[str] = []

        # Save current HEAD so we can roll back on failure
        head_sha = await self._get_head_sha(main_branch)

        for bead in batch:
            if not bead.branch_name:
                # Bead has no branch (maybe it did no work) — skip
                result.merged.append(bead.id)
                await self.db.update_bead_status(bead.id, BeadStatus.MERGED)
                merged_in_batch.append(bead.id)
                continue

            # Check that the branch exists
            branch_exists = await self._branch_exists(bead.branch_name)
            if not branch_exists:
                result.rejected.append(bead.id)
                result.conflicts[bead.id] = f"Branch {bead.branch_name} not found"
                await self.db.update_bead_status(bead.id, BeadStatus.REJECTED)
                await self.db.log_event(
                    "refinery_reject",
                    f"Branch {bead.branch_name} not found",
                    bead_id=bead.id,
                )
                # Roll back
                await self._reset_to(head_sha, main_branch)
                return False

            await self.db.update_bead_status(bead.id, BeadStatus.MERGING)

            ok, conflict_msg = await self._git_merge(bead.branch_name, bead.id, main_branch)
            if ok:
                result.merged.append(bead.id)
                await self.db.update_bead_status(bead.id, BeadStatus.MERGED)
                merged_in_batch.append(bead.id)
                await self.db.log_event(
                    "refinery_merged",
                    f"Merged {bead.branch_name} into {main_branch}",
                    bead_id=bead.id,
                )
            else:
                result.conflicts[bead.id] = conflict_msg
                # Abort merge and roll back to pre-batch HEAD
                await self._abort_merge()
                await self._reset_to(head_sha, main_branch)
                # Undo any already-merged beads in this batch
                for merged_id in merged_in_batch:
                    result.merged.remove(merged_id)
                return False

        return True

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------

    async def _get_main_branch(self) -> str:
        """Return 'main' or 'master' depending on what exists."""
        for branch in ("main", "master"):
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--verify", branch,
                cwd=self.rig.repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, _ = await proc.communicate()
            if proc.returncode == 0:
                return branch
        # Default fallback
        return "main"

    async def _get_head_sha(self, branch: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", branch,
            cwd=self.rig.repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip()

    async def _branch_exists(self, branch: str) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--verify", branch,
            cwd=self.rig.repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0

    async def _git_merge(
        self, branch: str, bead_id: str, onto: str
    ) -> tuple[bool, str]:
        """Attempt a --no-ff merge. Returns (success, conflict_message)."""
        # Make sure we're on the right branch
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", onto,
            cwd=self.rig.repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        proc = await asyncio.create_subprocess_exec(
            "git", "merge", "--no-ff", "-m", f"Merge bead/{bead_id}", branch,
            cwd=self.rig.repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return True, ""
        return False, (stdout.decode() + stderr.decode()).strip()[:500]

    async def _abort_merge(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            "git", "merge", "--abort",
            cwd=self.rig.repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def _reset_to(self, sha: str, branch: str) -> None:
        if not sha:
            return
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", branch,
            cwd=self.rig.repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        proc = await asyncio.create_subprocess_exec(
            "git", "reset", "--hard", sha,
            cwd=self.rig.repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
