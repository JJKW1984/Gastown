"""Pydantic v2 data models for Gastown."""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def gen_bead_id() -> str:
    """Generate a Gastown bead ID like gt-abc12."""
    letters = string.ascii_lowercase
    digits = string.digits
    suffix = "".join(secrets.choice(letters) for _ in range(3)) + "".join(
        secrets.choice(digits) for _ in range(2)
    )
    return f"gt-{suffix}"


def gen_convoy_id() -> str:
    """Generate a convoy ID."""
    return "convoy-" + "".join(secrets.choice(string.hexdigits[:16]) for _ in range(8))


class BeadStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
    MERGING = "merging"
    MERGED = "merged"
    REJECTED = "rejected"


class Bead(BaseModel):
    """An atomic unit of work (issue) tracked through the system."""

    id: str = Field(default_factory=gen_bead_id)
    rig_id: str
    title: str
    description: str
    status: BeadStatus = BeadStatus.PENDING
    priority: int = 0
    convoy_id: Optional[str] = None
    polecat_id: Optional[str] = None
    branch_name: Optional[str] = None
    worktree_path: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Convoy(BaseModel):
    """A group of beads dispatched together."""

    id: str = Field(default_factory=gen_convoy_id)
    rig_id: str
    bead_ids: list[str]
    status: str = "active"
    created_at: datetime = Field(default_factory=_utcnow)


class Rig(BaseModel):
    """A project container wrapping a git repository."""

    id: str
    name: str
    repo_path: str
    description: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


class PoleCAT(BaseModel):
    """In-memory record of a running worker agent (not persisted)."""

    id: str
    bead_id: str
    status: str = "running"
    messages: list[dict] = Field(default_factory=list)
    tool_call_count: int = 0
    last_heartbeat: datetime = Field(default_factory=_utcnow)
    nudge_count: int = 0


class WitnessEvent(BaseModel):
    """Event emitted by a PoleCAT to the shared monitoring queue."""

    polecat_id: str
    bead_id: str
    event_type: str  # heartbeat | done | failed | nudge | stuck
    details: str = ""
    timestamp: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Mayor structured output models
# ---------------------------------------------------------------------------


class BeadSpec(BaseModel):
    """A single bead as decomposed by the Mayor."""

    title: str
    description: str
    priority: int = 0
    estimated_files: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class DecompositionResult(BaseModel):
    """Full decomposition output from the Mayor."""

    beads: list[BeadSpec]
    summary: str


# ---------------------------------------------------------------------------
# Orchestrator result
# ---------------------------------------------------------------------------


class RunResult(BaseModel):
    """Result returned after a full orchestration run."""

    run_id: str
    rig_id: str
    goal: str
    beads: list[Bead]
    merged: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    mayor_review: str = ""
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = None
