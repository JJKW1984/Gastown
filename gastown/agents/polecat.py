"""PoleCAT — ephemeral worker agent.

Each PoleCAT handles exactly one bead. It runs an agentic tool-use loop,
executes filesystem and shell tools, and signals completion via done_signal.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import uuid
from datetime import datetime, timezone
from typing import Optional

from gastown.agents.base import BaseAgent
from gastown.models import Bead, BeadStatus, Rig, WitnessEvent
from gastown.storage import GastownDB

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-call format — LiteLLM translates per provider)
# ---------------------------------------------------------------------------

POLECAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Use relative paths from the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file, creating it and any missing parent directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
                    "content": {"type": "string", "description": "Full file content"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and directories at a path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path ('.' for working dir)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command (e.g. git, python, pytest). "
                "30-second timeout. Output is returned as a string."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done_signal",
            "description": (
                "Call this when you have finished your task. "
                "Provide a summary of what you changed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "What you changed and why"},
                    "files_changed": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of files you modified",
                    },
                },
                "required": ["summary", "files_changed"],
            },
        },
    },
]

MAX_TOOL_CALLS = 30
WRAP_UP_THRESHOLD = 25
COMMAND_TIMEOUT = 30


def _polecat_system(bead: Bead, rig: Rig) -> str:
    workdir = bead.worktree_path or rig.repo_path
    return (
        f"You are a Polecat, an expert coding agent. You work on exactly one task.\n\n"
        f"Bead ID: {bead.id}\n"
        f"Task: {bead.title}\n"
        f"Description: {bead.description}\n"
        f"Repository: {rig.repo_path}\n"
        f"Working directory: {workdir}\n\n"
        "Propulsion Principle: Begin immediately. Do not ask for confirmation. "
        "Do not wait. Make the changes, then call done_signal.\n\n"
        "Available tools: read_file, write_file, list_directory, run_command, done_signal\n"
        "All file paths are relative to your working directory.\n"
        "When you are done, ALWAYS call done_signal — never just stop."
    )


class PoleCAT(BaseAgent):
    """Ephemeral worker agent that executes a single bead."""

    def __init__(self, db: GastownDB, rig: Rig) -> None:
        super().__init__(db, rig)
        self._polecat_id = f"polecat-{uuid.uuid4().hex[:8]}"

    async def execute(
        self,
        bead: Bead,
        event_queue: asyncio.Queue,
        nudge_queue: Optional[asyncio.Queue] = None,
    ) -> dict:
        """Run the agentic tool-use loop for a single bead.

        Returns a dict with keys: status ("done" | "failed"), summary, files_changed.
        Emits WitnessEvent objects to event_queue throughout execution.
        """
        workdir = bead.worktree_path or self.rig.repo_path
        system = _polecat_system(bead, self.rig)
        messages: list[dict] = [
            {"role": "user", "content": f"Please complete this task: {bead.description}"}
        ]

        await event_queue.put(WitnessEvent(
            polecat_id=self._polecat_id,
            bead_id=bead.id,
            event_type="heartbeat",
            details="PoleCAT started",
        ))
        await self.db.log_event("polecat_start", f"PoleCAT {self._polecat_id} started", bead_id=bead.id, polecat_id=self._polecat_id)

        tool_call_count = 0

        try:
            while tool_call_count < MAX_TOOL_CALLS:
                # Check for a pending nudge
                if nudge_queue and not nudge_queue.empty():
                    try:
                        nudge_queue.get_nowait()
                        messages.append({
                            "role": "user",
                            "content": (
                                "You seem stuck. Please continue with your task. "
                                "If you are done, call done_signal immediately."
                            ),
                        })
                    except asyncio.QueueEmpty:
                        pass

                # Inject wrap-up hint near the limit
                if tool_call_count == WRAP_UP_THRESHOLD:
                    messages.append({
                        "role": "user",
                        "content": (
                            "You are approaching the tool call limit. "
                            "Finish what you're doing and call done_signal now."
                        ),
                    })

                response = await self._call_llm(
                    messages=messages,
                    system=system,
                    tools=POLECAT_TOOLS,
                )

                # Heartbeat after each LLM round
                await event_queue.put(WitnessEvent(
                    polecat_id=self._polecat_id,
                    bead_id=bead.id,
                    event_type="heartbeat",
                    details=f"LLM round complete (tool_calls={tool_call_count})",
                ))

                stop_reason = self._stop_reason(response)
                tool_calls = self._extract_tool_calls(response)
                assistant_text = self._extract_text(response)

                # Build assistant message for history
                choice_msg = response.choices[0].message
                assistant_msg: dict = {"role": "assistant"}
                if assistant_text:
                    assistant_msg["content"] = assistant_text
                if choice_msg.tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in choice_msg.tool_calls
                    ]
                messages.append(assistant_msg)

                if stop_reason == "stop" and not tool_calls:
                    # Claude stopped without done_signal — treat as done
                    await event_queue.put(WitnessEvent(
                        polecat_id=self._polecat_id,
                        bead_id=bead.id,
                        event_type="done",
                        details="LLM stopped naturally",
                    ))
                    await self._commit_work(bead, workdir, "Task completed")
                    await self.db.update_bead_status(bead.id, BeadStatus.DONE, metadata_summary="Task completed")
                    return {"status": "done", "summary": "Task completed", "files_changed": []}

                # Process tool calls
                tool_results = []
                done_result: Optional[dict] = None

                for tc in tool_calls:
                    tool_call_count += 1
                    name = tc["name"]
                    args = tc["arguments"]
                    call_id = tc["id"]

                    if name == "done_signal":
                        done_result = args
                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": "Task complete. Committing work.",
                        })
                    else:
                        output = await self._execute_tool(name, args, workdir)
                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output,
                        })

                messages.extend(tool_results)

                if done_result is not None:
                    summary = done_result.get("summary", "Task completed")
                    files_changed = done_result.get("files_changed", [])
                    await self._commit_work(bead, workdir, summary)
                    await self.db.update_bead_status(bead.id, BeadStatus.DONE)
                    await self.db.log_event(
                        "polecat_done",
                        f"PoleCAT done: {summary[:200]}",
                        bead_id=bead.id,
                        polecat_id=self._polecat_id,
                    )
                    await event_queue.put(WitnessEvent(
                        polecat_id=self._polecat_id,
                        bead_id=bead.id,
                        event_type="done",
                        details=summary[:200],
                    ))
                    return {"status": "done", "summary": summary, "files_changed": files_changed}

            # Hit MAX_TOOL_CALLS
            await self._commit_work(bead, workdir, "Reached tool call limit")
            await self.db.update_bead_status(bead.id, BeadStatus.DONE)
            await event_queue.put(WitnessEvent(
                polecat_id=self._polecat_id,
                bead_id=bead.id,
                event_type="done",
                details="Completed at tool call limit",
            ))
            return {"status": "done", "summary": "Completed at tool call limit", "files_changed": []}

        except Exception as exc:
            await self.db.update_bead_status(bead.id, BeadStatus.FAILED)
            await self.db.log_event(
                "polecat_failed",
                str(exc)[:500],
                bead_id=bead.id,
                polecat_id=self._polecat_id,
            )
            await event_queue.put(WitnessEvent(
                polecat_id=self._polecat_id,
                bead_id=bead.id,
                event_type="failed",
                details=str(exc)[:200],
            ))
            return {"status": "failed", "summary": str(exc), "files_changed": []}

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def _execute_tool(self, name: str, args: dict, workdir: str) -> str:
        try:
            if name == "read_file":
                return await self._tool_read_file(args["path"], workdir)
            elif name == "write_file":
                return await self._tool_write_file(args["path"], args["content"], workdir)
            elif name == "list_directory":
                return await self._tool_list_directory(args.get("path", "."), workdir)
            elif name == "run_command":
                return await self._tool_run_command(args["command"], workdir)
            else:
                return f"Unknown tool: {name}"
        except Exception as exc:
            return f"Error in {name}: {exc}"

    async def _tool_read_file(self, path: str, workdir: str) -> str:
        safe_path = self._safe_path(path, workdir)
        if not safe_path.exists():
            return f"File not found: {path}"
        content = safe_path.read_text(encoding="utf-8", errors="replace")
        # Truncate very large files
        if len(content) > 50_000:
            content = content[:50_000] + f"\n... (truncated, total {len(content)} chars)"
        return content

    async def _tool_write_file(self, path: str, content: str, workdir: str) -> str:
        safe_path = self._safe_path(path, workdir)
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_text(content, encoding="utf-8")
        return f"Written: {path} ({len(content)} chars)"

    async def _tool_list_directory(self, path: str, workdir: str) -> str:
        safe_path = self._safe_path(path, workdir)
        if not safe_path.exists():
            return f"Directory not found: {path}"
        if not safe_path.is_dir():
            return f"Not a directory: {path}"
        entries = []
        for entry in sorted(safe_path.iterdir()):
            tag = "[dir]" if entry.is_dir() else "[file]"
            entries.append(f"{tag} {entry.name}")
        return "\n".join(entries) if entries else "(empty)"

    async def _tool_run_command(self, command: str, workdir: str) -> str:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=COMMAND_TIMEOUT)
            output = stdout.decode(errors="replace").strip()
            exit_code = proc.returncode
            result = output[:10_000]  # cap output
            if exit_code != 0:
                result = f"[exit {exit_code}]\n{result}"
            return result or "(no output)"
        except asyncio.TimeoutError:
            return f"[timeout after {COMMAND_TIMEOUT}s]"

    def _safe_path(self, path: str, workdir: str) -> pathlib.Path:
        """Resolve path relative to workdir, blocking traversal outside it."""
        base = pathlib.Path(workdir).resolve()
        resolved = (base / path).resolve()
        if not str(resolved).startswith(str(base)):
            raise ValueError(f"Path traversal blocked: {path}")
        return resolved

    # ------------------------------------------------------------------
    # Git commit
    # ------------------------------------------------------------------

    async def _commit_work(self, bead: Bead, workdir: str, summary: str) -> None:
        """Stage all changes in the worktree and commit them."""
        # git add -A
        add_proc = await asyncio.create_subprocess_exec(
            "git", "add", "-A",
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await add_proc.communicate()

        # Check if there's anything to commit
        status_proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain",
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await status_proc.communicate()
        if not stdout.strip():
            return  # Nothing to commit

        commit_msg = f"[{bead.id}] {summary[:72]}"
        commit_proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", commit_msg,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await commit_proc.communicate()
