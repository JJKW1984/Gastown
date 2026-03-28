"""SQLite-backed persistence for Gastown beads, rigs, convoys, and events."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from gastown.models import Bead, BeadStatus, Convoy, Rig


class GastownDB:
    """Thread-safe (via asyncio.Lock) SQLite database for Gastown state."""

    def __init__(self, db_path: str = "gastown.db") -> None:
        self._db_path = db_path
        self._lock = asyncio.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create tables and configure SQLite pragmas."""
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        async with self._lock:
            self._conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS rigs (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    repo_path   TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    created_at  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS beads (
                    id            TEXT PRIMARY KEY,
                    rig_id        TEXT NOT NULL,
                    title         TEXT NOT NULL,
                    description   TEXT NOT NULL,
                    status        TEXT NOT NULL DEFAULT 'pending',
                    priority      INTEGER DEFAULT 0,
                    convoy_id     TEXT,
                    polecat_id    TEXT,
                    branch_name   TEXT,
                    worktree_path TEXT,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL,
                    metadata      TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS convoys (
                    id        TEXT PRIMARY KEY,
                    rig_id    TEXT NOT NULL,
                    bead_ids  TEXT NOT NULL,
                    status    TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    bead_id     TEXT,
                    polecat_id  TEXT,
                    event_type  TEXT NOT NULL,
                    details     TEXT DEFAULT '',
                    timestamp   TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()

    # ------------------------------------------------------------------
    # Rigs
    # ------------------------------------------------------------------

    async def create_rig(self, rig: Rig) -> None:
        async with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO rigs (id, name, repo_path, description, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (rig.id, rig.name, rig.repo_path, rig.description, rig.created_at.isoformat()),
            )
            self._conn.commit()

    async def get_rig(self, rig_id: str) -> Optional[Rig]:
        row = self._conn.execute("SELECT * FROM rigs WHERE id = ?", (rig_id,)).fetchone()
        if not row:
            return None
        return Rig(
            id=row["id"],
            name=row["name"],
            repo_path=row["repo_path"],
            description=row["description"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    async def list_rigs(self) -> list[Rig]:
        rows = self._conn.execute("SELECT * FROM rigs ORDER BY created_at DESC").fetchall()
        return [
            Rig(
                id=r["id"],
                name=r["name"],
                repo_path=r["repo_path"],
                description=r["description"] or "",
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Beads
    # ------------------------------------------------------------------

    async def create_bead(self, bead: Bead) -> None:
        async with self._lock:
            self._conn.execute(
                "INSERT INTO beads (id, rig_id, title, description, status, priority, "
                "convoy_id, polecat_id, branch_name, worktree_path, created_at, updated_at, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    bead.id,
                    bead.rig_id,
                    bead.title,
                    bead.description,
                    bead.status.value,
                    bead.priority,
                    bead.convoy_id,
                    bead.polecat_id,
                    bead.branch_name,
                    bead.worktree_path,
                    bead.created_at.isoformat(),
                    bead.updated_at.isoformat(),
                    json.dumps(bead.metadata),
                ),
            )
            self._conn.commit()

    async def get_bead(self, bead_id: str) -> Optional[Bead]:
        row = self._conn.execute("SELECT * FROM beads WHERE id = ?", (bead_id,)).fetchone()
        return _row_to_bead(row) if row else None

    # Whitelist of columns that may be updated via update_bead_status kwargs.
    # Prevents SQL injection by rejecting any kwarg key not in this set.
    _BEAD_UPDATABLE_COLUMNS = frozenset({
        "convoy_id", "polecat_id", "branch_name", "worktree_path",
        "priority", "metadata_summary",
    })

    async def update_bead_status(self, bead_id: str, status: BeadStatus, **kwargs) -> None:
        now = datetime.now(timezone.utc).isoformat()
        # Validate kwarg keys against whitelist to prevent SQL injection
        unknown = set(kwargs) - self._BEAD_UPDATABLE_COLUMNS
        if unknown:
            raise ValueError(f"Unknown bead column(s): {unknown}")

        async with self._lock:
            fields: dict[str, object] = {"status": status.value, "updated_at": now}
            fields.update({k: v for k, v in kwargs.items() if v is not None})
            # Column names are from the whitelist — safe to interpolate
            set_clause = ", ".join(f"{col} = ?" for col in fields)
            values = list(fields.values()) + [bead_id]
            self._conn.execute(  # nosec B608 — columns from whitelist only
                "UPDATE beads SET " + set_clause + " WHERE id = ?", values
            )
            self._conn.commit()

    async def list_beads(
        self, rig_id: str, status: Optional[BeadStatus] = None
    ) -> list[Bead]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM beads WHERE rig_id = ? AND status = ? ORDER BY priority, created_at",
                (rig_id, status.value),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM beads WHERE rig_id = ? ORDER BY priority, created_at",
                (rig_id,),
            ).fetchall()
        return [_row_to_bead(r) for r in rows if r]

    # ------------------------------------------------------------------
    # Convoys
    # ------------------------------------------------------------------

    async def create_convoy(self, convoy: Convoy) -> None:
        async with self._lock:
            self._conn.execute(
                "INSERT INTO convoys (id, rig_id, bead_ids, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    convoy.id,
                    convoy.rig_id,
                    json.dumps(convoy.bead_ids),
                    convoy.status,
                    convoy.created_at.isoformat(),
                ),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    async def log_event(
        self,
        event_type: str,
        details: str = "",
        bead_id: Optional[str] = None,
        polecat_id: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            self._conn.execute(
                "INSERT INTO events (bead_id, polecat_id, event_type, details, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (bead_id, polecat_id, event_type, details, now),
            )
            self._conn.commit()

    async def get_events(self, bead_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE bead_id = ? ORDER BY timestamp",
            (bead_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    async def get_status_counts(self, rig_id: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM beads WHERE rig_id = ? GROUP BY status",
            (rig_id,),
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _row_to_bead(row: sqlite3.Row) -> Bead:
    return Bead(
        id=row["id"],
        rig_id=row["rig_id"],
        title=row["title"],
        description=row["description"],
        status=BeadStatus(row["status"]),
        priority=row["priority"] or 0,
        convoy_id=row["convoy_id"],
        polecat_id=row["polecat_id"],
        branch_name=row["branch_name"],
        worktree_path=row["worktree_path"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        metadata=json.loads(row["metadata"] or "{}"),
    )
