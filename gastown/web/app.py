"""FastAPI web application for Gastown.

Provides:
- REST API for rigs, beads, runs, and logs
- WebSocket endpoint for real-time run progress streaming
- Static HTML dashboard served at GET /
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from gastown.models import BeadStatus, Rig, RunResult, WitnessEvent, gen_bead_id
from gastown.orchestrator import GastownOrchestrator
from gastown.storage import GastownDB


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class CreateRigRequest(BaseModel):
    path: str
    name: str
    description: str = ""


class StartRunRequest(BaseModel):
    goal: str
    rig_id: str
    max_concurrent: int = 4


class RunStatus(BaseModel):
    run_id: str
    status: str  # running | completed | failed
    result: Optional[dict] = None
    started_at: str
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------

_db: Optional[GastownDB] = None
_active_runs: dict[str, asyncio.Task] = {}
_run_results: dict[str, RunResult] = {}
_run_errors: dict[str, str] = {}
_run_started: dict[str, str] = {}
_run_event_queues: dict[str, asyncio.Queue] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db
    db_path = os.getenv("GASTOWN_DB_PATH", "gastown.db")
    _db = GastownDB(db_path)
    await _db.initialize()
    yield
    if _db:
        _db.close()


def _get_db() -> GastownDB:
    if _db is None:
        raise RuntimeError("Database not initialized")
    return _db


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Gastown",
    description="Multi-agent engineering coordinator",
    version="0.1.0",
    lifespan=lifespan,
)

# Serve static files
_static_dir = os.path.join(os.path.dirname(__file__), "static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(os.path.join(_static_dir, "index.html"))


# ---------------------------------------------------------------------------
# Rig endpoints
# ---------------------------------------------------------------------------


@app.get("/api/rigs")
async def list_rigs():
    db = _get_db()
    rigs = await db.list_rigs()
    return [r.model_dump(mode="json") for r in rigs]


@app.post("/api/rigs", status_code=201)
async def create_rig(req: CreateRigRequest):
    db = _get_db()
    repo_path = os.path.abspath(req.path)
    if not os.path.isdir(repo_path):
        raise HTTPException(status_code=400, detail=f"Directory not found: {repo_path}")

    rig_id = req.name.lower().replace(" ", "-") + "-" + uuid.uuid4().hex[:6]
    rig = Rig(
        id=rig_id,
        name=req.name,
        repo_path=repo_path,
        description=req.description,
    )
    await db.create_rig(rig)
    return rig.model_dump(mode="json")


@app.get("/api/rigs/{rig_id}")
async def get_rig(rig_id: str):
    db = _get_db()
    rig = await db.get_rig(rig_id)
    if not rig:
        raise HTTPException(status_code=404, detail="Rig not found")
    return rig.model_dump(mode="json")


@app.get("/api/rigs/{rig_id}/status")
async def rig_status(rig_id: str):
    db = _get_db()
    counts = await db.get_status_counts(rig_id)
    return counts


@app.get("/api/rigs/{rig_id}/beads")
async def list_beads(rig_id: str, status: Optional[str] = None):
    db = _get_db()
    bs = BeadStatus(status) if status else None
    beads = await db.list_beads(rig_id, bs)
    return [b.model_dump(mode="json") for b in beads]


# ---------------------------------------------------------------------------
# Bead endpoints
# ---------------------------------------------------------------------------


@app.get("/api/beads/{bead_id}")
async def get_bead(bead_id: str):
    db = _get_db()
    bead = await db.get_bead(bead_id)
    if not bead:
        raise HTTPException(status_code=404, detail="Bead not found")
    return bead.model_dump(mode="json")


@app.get("/api/beads/{bead_id}/logs")
async def bead_logs(bead_id: str):
    db = _get_db()
    events = await db.get_events(bead_id)
    return events


# ---------------------------------------------------------------------------
# Run endpoints
# ---------------------------------------------------------------------------


@app.post("/api/runs", status_code=202)
async def start_run(req: StartRunRequest):
    db = _get_db()
    rig = await db.get_rig(req.rig_id)
    if not rig:
        raise HTTPException(status_code=404, detail=f"Rig '{req.rig_id}' not found")

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    _run_started[run_id] = started_at

    # Per-run event queue so the WebSocket can tap in
    event_queue: asyncio.Queue[WitnessEvent] = asyncio.Queue()
    _run_event_queues[run_id] = event_queue

    orchestrator = GastownOrchestrator(
        db=db,
        max_concurrent=req.max_concurrent,
    )

    async def _run_task():
        async def _progress(event: WitnessEvent):
            await event_queue.put(event)

        try:
            result = await orchestrator.run(req.goal, rig, progress_callback=_progress)
            _run_results[run_id] = result
        except Exception as exc:
            _run_errors[run_id] = str(exc)
        finally:
            # Sentinel to signal WebSocket clients the run is over
            await event_queue.put(None)  # type: ignore[arg-type]

    task = asyncio.create_task(_run_task())
    _active_runs[run_id] = task

    return {"run_id": run_id, "started_at": started_at}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    if run_id in _run_results:
        return RunStatus(
            run_id=run_id,
            status="completed",
            result=_run_results[run_id].model_dump(mode="json"),
            started_at=_run_started.get(run_id, ""),
        ).model_dump(mode="json")
    if run_id in _run_errors:
        return RunStatus(
            run_id=run_id,
            status="failed",
            error=_run_errors[run_id],
            started_at=_run_started.get(run_id, ""),
        ).model_dump(mode="json")
    if run_id in _active_runs:
        return RunStatus(
            run_id=run_id,
            status="running",
            started_at=_run_started.get(run_id, ""),
        ).model_dump(mode="json")
    raise HTTPException(status_code=404, detail="Run not found")


# ---------------------------------------------------------------------------
# WebSocket — real-time run streaming
# ---------------------------------------------------------------------------


@app.websocket("/ws/runs/{run_id}")
async def run_websocket(websocket: WebSocket, run_id: str):
    await websocket.accept()

    event_queue = _run_event_queues.get(run_id)
    if event_queue is None:
        await websocket.send_json({"error": f"Run {run_id} not found"})
        await websocket.close()
        return

    try:
        while True:
            event = await event_queue.get()
            if event is None:
                # Run finished — send final result
                if run_id in _run_results:
                    result = _run_results[run_id]
                    await websocket.send_json({
                        "event_type": "run_complete",
                        "result": result.model_dump(mode="json"),
                    })
                elif run_id in _run_errors:
                    await websocket.send_json({
                        "event_type": "run_failed",
                        "error": _run_errors[run_id],
                    })
                break
            else:
                await websocket.send_json(event.model_dump(mode="json"))
    except WebSocketDisconnect:
        pass
    finally:
        _run_event_queues.pop(run_id, None)
