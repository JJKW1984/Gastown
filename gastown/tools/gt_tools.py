"""gt_tools — internal Gastown operations (worktree management, nudging, status)."""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from gastown.models import Bead, BeadStatus, Rig
from gastown.storage import GastownDB


async def gt_setup_worktree(bead: Bead, rig: Rig) -> str:
    """Create a git worktree for a bead's PoleCAT to work in.

    Returns the absolute path to the new worktree directory.
    Raises RuntimeError if git worktree creation fails.
    """
    branch = f"bead/{bead.id}"
    worktrees_dir = os.path.join(rig.repo_path, ".worktrees")
    worktree_path = os.path.join(worktrees_dir, bead.id)
    os.makedirs(worktrees_dir, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "add", "-b", branch, worktree_path,
        cwd=rig.repo_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"git worktree add failed for {bead.id}: {stderr.decode().strip()}"
        )
    return worktree_path


async def gt_teardown_worktree(bead: Bead, rig: Rig) -> None:
    """Remove a bead's git worktree after its work has been merged."""
    worktree_path = os.path.join(rig.repo_path, ".worktrees", bead.id)
    if not os.path.exists(worktree_path):
        return

    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "remove", "--force", worktree_path,
        cwd=rig.repo_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    # Also prune stale worktree metadata
    await asyncio.create_subprocess_exec(
        "git", "worktree", "prune",
        cwd=rig.repo_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )


async def gt_status(rig_id: str, db: GastownDB) -> dict[str, int]:
    """Return bead counts by status for a rig."""
    return await db.get_status_counts(rig_id)


async def gt_nudge(
    bead_id: str,
    db: GastownDB,
    nudge_queues: dict[str, asyncio.Queue],
) -> None:
    """Send a nudge to a stuck PoleCAT via its per-agent queue."""
    await db.log_event("nudge", f"Witness nudged polecat for bead {bead_id}", bead_id=bead_id)
    if bead_id in nudge_queues:
        await nudge_queues[bead_id].put("nudge")


async def gt_ensure_initial_commit(repo_path: str) -> None:
    """Ensure the repo has at least one commit (required for git worktrees)."""
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "HEAD",
        cwd=repo_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        # No commits yet — create an empty initial commit
        proc2 = await asyncio.create_subprocess_exec(
            "git", "commit", "--allow-empty", "-m", "Initial commit (Gastown)",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc2.communicate()


async def gt_get_file_tree(repo_path: str, max_files: int = 200) -> str:
    """Return a compact file tree string using git ls-files."""
    proc = await asyncio.create_subprocess_exec(
        "git", "ls-files",
        cwd=repo_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0 or not stdout.strip():
        # Fallback: walk the directory
        lines = []
        for root, dirs, files in os.walk(repo_path):
            # Skip .git and .worktrees
            dirs[:] = [d for d in dirs if d not in (".git", ".worktrees", "__pycache__", "node_modules")]
            rel_root = os.path.relpath(root, repo_path)
            for f in files:
                path = os.path.join(rel_root, f) if rel_root != "." else f
                lines.append(path)
                if len(lines) >= max_files:
                    lines.append("... (truncated)")
                    return "\n".join(lines)
        return "\n".join(lines) or "(empty repository)"

    files = stdout.decode().strip().splitlines()
    if len(files) > max_files:
        files = files[:max_files] + [f"... ({len(files) - max_files} more files)"]
    return "\n".join(files)
