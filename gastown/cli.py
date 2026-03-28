"""Gastown CLI — Click-based command interface."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Optional

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

load_dotenv()

console = Console()

STATUS_COLORS = {
    "pending":     "white",
    "in_progress": "yellow",
    "done":        "green",
    "failed":      "red",
    "merged":      "bright_blue",
    "rejected":    "magenta",
    "merging":     "cyan",
    "blocked":     "orange3",
}


def _colored_status(status: str) -> Text:
    color = STATUS_COLORS.get(status, "white")
    return Text(status, style=color)


def _get_db():
    from gastown.storage import GastownDB
    db_path = os.getenv("GASTOWN_DB_PATH", "gastown.db")
    db = GastownDB(db_path)
    asyncio.get_event_loop().run_until_complete(db.initialize())
    return db


@click.group()
def main():
    """🏙️  Gastown — multi-agent engineering coordinator."""


# ---------------------------------------------------------------------------
# gastown init
# ---------------------------------------------------------------------------

@main.command()
@click.argument("path", default=".")
@click.option("--name", "-n", default=None, help="Rig name (defaults to directory name)")
@click.option("--description", "-d", default="", help="Optional description")
def init(path: str, name: Optional[str], description: str):
    """Initialize a git repository as a Gastown rig."""
    async def _run():
        import uuid
        from gastown.models import Rig
        from gastown.storage import GastownDB
        from gastown.tools.gt_tools import gt_ensure_initial_commit

        repo_path = os.path.abspath(path)
        if not os.path.isdir(repo_path):
            console.print(f"[red]Error:[/] Directory not found: {repo_path}")
            sys.exit(1)

        rig_name = name or os.path.basename(repo_path)
        rig_id = rig_name.lower().replace(" ", "-") + "-" + uuid.uuid4().hex[:6]

        db_path = os.getenv("GASTOWN_DB_PATH", "gastown.db")
        db = GastownDB(db_path)
        await db.initialize()

        # Ensure at least one commit exists (required for git worktrees)
        await gt_ensure_initial_commit(repo_path)

        rig = Rig(
            id=rig_id,
            name=rig_name,
            repo_path=repo_path,
            description=description,
        )
        await db.create_rig(rig)
        db.close()

        console.print(f"[green]✓[/] Rig created: [bold]{rig_name}[/] ({rig_id})")
        console.print(f"  Path: {repo_path}")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# gastown run
# ---------------------------------------------------------------------------

@main.command()
@click.argument("goal")
@click.option("--rig", "-r", default=None, help="Rig ID or path. Defaults to current directory.")
@click.option("--max-concurrent", "-j", default=4, show_default=True, help="Max simultaneous polecats")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def run(goal: str, rig: Optional[str], max_concurrent: int, yes: bool):
    """Decompose a goal and dispatch polecats to accomplish it."""
    async def _run():
        from gastown.models import WitnessEvent
        from gastown.orchestrator import GastownOrchestrator
        from gastown.storage import GastownDB

        db_path = os.getenv("GASTOWN_DB_PATH", "gastown.db")
        db = GastownDB(db_path)
        await db.initialize()

        # Resolve rig
        rig_obj = None
        if rig:
            rig_obj = await db.get_rig(rig)
            if not rig_obj:
                # Try treating it as a path
                import uuid
                from gastown.models import Rig
                from gastown.tools.gt_tools import gt_ensure_initial_commit
                repo_path = os.path.abspath(rig)
                if os.path.isdir(repo_path):
                    name = os.path.basename(repo_path)
                    rig_id = name.lower() + "-" + uuid.uuid4().hex[:6]
                    await gt_ensure_initial_commit(repo_path)
                    rig_obj = Rig(id=rig_id, name=name, repo_path=repo_path)
                    await db.create_rig(rig_obj)
                else:
                    console.print(f"[red]Error:[/] Rig not found: {rig}")
                    sys.exit(1)
        else:
            # Use first rig or create one from cwd
            rigs = await db.list_rigs()
            if rigs:
                rig_obj = rigs[0]
                console.print(f"Using rig: [bold]{rig_obj.name}[/] ({rig_obj.id})")
            else:
                import uuid
                from gastown.models import Rig
                from gastown.tools.gt_tools import gt_ensure_initial_commit
                cwd = os.getcwd()
                name = os.path.basename(cwd)
                rig_id = name.lower() + "-" + uuid.uuid4().hex[:6]
                await gt_ensure_initial_commit(cwd)
                rig_obj = Rig(id=rig_id, name=name, repo_path=cwd)
                await db.create_rig(rig_obj)
                console.print(f"Created rig from cwd: [bold]{name}[/]")

        console.print(f"\n[bold yellow]Mayor[/] is analyzing your goal…\n")

        # Decompose first to show preview
        from gastown.agents.mayor import Mayor
        mayor = Mayor(db=db, rig=rig_obj)

        with console.status("[yellow]Decomposing goal…"):
            beads = await mayor.decompose(goal, rig_obj)

        # Show the proposed beads table
        table = Table(title="Proposed Beads", show_header=True, header_style="bold")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Title")
        table.add_column("Priority", justify="right")
        for b in beads:
            table.add_row(b.id, b.title, str(b.priority))
        console.print(table)

        if not yes:
            click.confirm(f"\nDispatch {len(beads)} polecat(s)?", abort=True)

        # Live status display
        live_statuses: dict[str, str] = {b.id: "pending" for b in beads}
        live_events: list[str] = []

        def _make_live_table() -> Table:
            t = Table(show_header=True, header_style="bold", expand=True)
            t.add_column("Bead", style="cyan", no_wrap=True)
            t.add_column("Title")
            t.add_column("Status")
            for b in beads:
                st = live_statuses.get(b.id, "pending")
                t.add_row(b.id, b.title[:50], _colored_status(st))
            return t

        async def _progress(event: WitnessEvent):
            if event.event_type == "done":
                live_statuses[event.bead_id] = "done"
            elif event.event_type == "failed":
                live_statuses[event.bead_id] = "failed"
            elif event.event_type == "heartbeat":
                live_statuses[event.bead_id] = "in_progress"
            live_events.append(f"[{event.bead_id}] {event.event_type}: {event.details[:60]}")

        orchestrator = GastownOrchestrator(db=db, max_concurrent=max_concurrent)

        with Live(_make_live_table(), refresh_per_second=2, console=console) as live:
            async def _run_with_updates():
                result = await orchestrator.run(goal, rig_obj, progress_callback=_progress)
                return result

            # We need to update the live table while waiting — use a background refresh
            refresh_task = asyncio.create_task(_periodic_refresh(live, _make_live_table))
            result = await _run_with_updates()
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass
            live.update(_make_live_table())

        # Summary
        console.print()
        console.print(f"[bold green]✓ Merged:[/]   {len(result.merged)} beads")
        if result.rejected:
            console.print(f"[bold red]✗ Rejected:[/] {len(result.rejected)} beads")
        console.print()
        console.print(f"[bold]Mayor's review:[/] {result.mayor_review}")
        db.close()

    asyncio.run(_run())


async def _periodic_refresh(live: "Live", make_table):
    """Refresh the live display every 0.5 seconds."""
    while True:
        await asyncio.sleep(0.5)
        live.update(make_table())


# ---------------------------------------------------------------------------
# gastown status
# ---------------------------------------------------------------------------

@main.command()
@click.option("--rig", "-r", default=None, help="Rig ID to filter")
def status(rig: Optional[str]):
    """Show bead status summary for all rigs (or a specific rig)."""
    async def _run():
        from gastown.storage import GastownDB

        db_path = os.getenv("GASTOWN_DB_PATH", "gastown.db")
        db = GastownDB(db_path)
        await db.initialize()
        rigs = await db.list_rigs()

        if not rigs:
            console.print("No rigs found. Run [bold]gastown init[/] first.")
            db.close()
            return

        for r in rigs:
            if rig and r.id != rig:
                continue
            counts = await db.get_status_counts(r.id)
            table = Table(title=f"Rig: {r.name} ({r.id})", show_header=True)
            table.add_column("Status")
            table.add_column("Count", justify="right")
            for st, cnt in sorted(counts.items()):
                table.add_row(_colored_status(st), str(cnt))
            console.print(table)

        db.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# gastown beads
# ---------------------------------------------------------------------------

@main.command()
@click.option("--rig", "-r", default=None, help="Rig ID to filter")
@click.option("--status-filter", "-s", default=None, help="Filter by status")
def beads(rig: Optional[str], status_filter: Optional[str]):
    """List beads with their current status."""
    async def _run():
        from gastown.models import BeadStatus
        from gastown.storage import GastownDB

        db_path = os.getenv("GASTOWN_DB_PATH", "gastown.db")
        db = GastownDB(db_path)
        await db.initialize()
        rigs = await db.list_rigs()

        sf = BeadStatus(status_filter) if status_filter else None

        for r in rigs:
            if rig and r.id != rig:
                continue
            bead_list = await db.list_beads(r.id, sf)
            if not bead_list:
                continue
            table = Table(title=f"Beads — {r.name}", show_header=True)
            table.add_column("ID", style="cyan", no_wrap=True)
            table.add_column("Title")
            table.add_column("Status")
            table.add_column("Priority", justify="right")
            for b in bead_list:
                table.add_row(b.id, b.title[:60], _colored_status(b.status), str(b.priority))
            console.print(table)

        db.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# gastown logs
# ---------------------------------------------------------------------------

@main.command()
@click.argument("bead_id")
def logs(bead_id: str):
    """Show the event log for a specific bead."""
    async def _run():
        from gastown.storage import GastownDB

        db_path = os.getenv("GASTOWN_DB_PATH", "gastown.db")
        db = GastownDB(db_path)
        await db.initialize()
        events = await db.get_events(bead_id)
        db.close()

        if not events:
            console.print(f"No events found for bead [bold]{bead_id}[/]")
            return

        table = Table(title=f"Events — {bead_id}", show_header=True)
        table.add_column("Time", style="dim", no_wrap=True)
        table.add_column("Type", style="yellow")
        table.add_column("Details")
        for ev in events:
            ts = ev.get("timestamp", "")[:19] if ev.get("timestamp") else ""
            table.add_row(ts, ev.get("event_type", ""), ev.get("details", "")[:100])
        console.print(table)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# gastown serve
# ---------------------------------------------------------------------------

@main.command()
@click.option("--host", default=None, help="Bind host (default: GASTOWN_HOST or 127.0.0.1)")
@click.option("--port", default=None, type=int, help="Port (default: GASTOWN_PORT or 8000)")
@click.option("--reload", is_flag=True, help="Enable auto-reload (dev mode)")
def serve(host: Optional[str], port: Optional[int], reload: bool):
    """Start the Gastown web server."""
    import uvicorn

    _host = host or os.getenv("GASTOWN_HOST", "127.0.0.1")
    _port = port or int(os.getenv("GASTOWN_PORT", "8000"))

    console.print(f"[bold green]Gastown web server starting at[/] http://{_host}:{_port}")
    uvicorn.run(
        "gastown.web.app:app",
        host=_host,
        port=_port,
        reload=reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
