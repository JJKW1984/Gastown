# Agent Development Guide

This guide explains how to build custom agents for Gastown, integrate them into the orchestrator, and test them effectively.

---

## Contents

- [BaseAgent Interface](#baseagent-interface)
- [LLM Call Helpers](#llm-call-helpers)
- [Tool Definition Schema](#tool-definition-schema)
- [System Prompt Best Practices](#system-prompt-best-practices)
- [Example Custom Agent](#example-custom-agent)
- [Unit and Integration Testing Patterns](#unit-and-integration-testing-patterns)
- [Integrating a Custom Agent into the Orchestrator](#integrating-a-custom-agent-into-the-orchestrator)

---

## BaseAgent Interface

All agents inherit from `gastown.agents.base.BaseAgent`.

```python
class BaseAgent:
    DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"
    MAX_TOKENS = 8096

    def __init__(self, db: GastownDB, rig: Rig) -> None:
        self.db = db
        self.rig = rig
        self.model = os.getenv("GASTOWN_MODEL", self.DEFAULT_MODEL)
```

The constructor receives a `GastownDB` instance and a `Rig`. It reads `GASTOWN_MODEL` at construction time so the model can be overridden per-process.

### Methods you inherit

| Method | Returns | Description |
|--------|---------|-------------|
| `_call_llm(messages, system, tools, response_format, tool_choice)` | `ModelResponse` | Async LiteLLM call |
| `_extract_text(response)` | `str` | Pull plain text from `choices[0].message.content` |
| `_extract_tool_calls(response)` | `list[dict]` | Parse `tool_calls` into `[{id, name, arguments}]` |
| `_stop_reason(response)` | `str` | `choices[0].finish_reason` |

### Methods you implement

Your agent class should provide `async` methods that call `self._call_llm()` and use the results.

---

## LLM Call Helpers

### `_call_llm`

```python
async def _call_llm(
    self,
    messages: list[dict],           # conversation history (no system message)
    system: str,                    # system prompt (prepended automatically)
    tools: list[dict] | None = None,
    response_format: Any = None,
    tool_choice: Any = None,
) -> ModelResponse:
```

The method prepends `{"role": "system", "content": system}` to the messages list before calling LiteLLM.

**Important**: `messages` should contain only `user` and `assistant` messages. Do not include the system message yourself.

### Working with tool calls

```python
response = await self._call_llm(messages=msgs, system=SYSTEM, tools=MY_TOOLS)

# Check if the model called a tool
tool_calls = self._extract_tool_calls(response)
for tc in tool_calls:
    name = tc["name"]          # tool function name
    args = tc["arguments"]     # dict of parsed arguments
    call_id = tc["id"]         # used when building tool result messages

# Append tool results to message history
messages.append({
    "role": "tool",
    "tool_call_id": call_id,
    "content": "result of the tool call",
})
```

### Forced tool call (structured output)

To force the model to return a specific JSON structure:

```python
response = await self._call_llm(
    messages=msgs,
    system=SYSTEM,
    tools=[my_tool_def],
    tool_choice={"type": "function", "function": {"name": "my_function"}},
)
```

This works across Anthropic, OpenAI, and Azure providers via LiteLLM's translation layer.

---

## Tool Definition Schema

Tools follow the OpenAI function-call format. LiteLLM translates them for each provider.

```python
MY_TOOL = {
    "type": "function",
    "function": {
        "name": "analyze_file",
        "description": "Analyze a source file for issues.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file",
                },
                "check_type": {
                    "type": "string",
                    "enum": ["security", "style", "performance"],
                    "description": "Type of analysis to perform",
                },
            },
            "required": ["path", "check_type"],
        },
    },
}
```

**Tips:**

- Keep descriptions concise but specific. The model uses them to decide when and how to call the tool.
- Mark non-optional parameters in `required`.
- Use `enum` for constrained string values.
- For structured output, generate `parameters` from a Pydantic model: `MyModel.model_json_schema()`.

---

## System Prompt Best Practices

1. **State the role clearly.** The model should immediately know what it is and what it should do.
2. **Be explicit about what NOT to do.** The Mayor's prompt says "NEVER write code yourself". This prevents drift.
3. **Specify the output format exactly.** If you need JSON, say "Output ONLY valid JSON matching the required schema. No preamble."
4. **Include scope.** Reference the specific bead, rig, or working directory so the model has context.
5. **Add an action trigger.** Something like "Begin immediately. Do not ask for confirmation." prevents the model from stalling with questions.

Example structure:

```python
SYSTEM = """You are a <role> in the Gastown system.

Your job:
- <primary responsibility>
- <constraint: what you must NOT do>

Rules:
1. <rule 1>
2. <rule 2>

Output: <exact format description>"""
```

---

## Example Custom Agent

The following implements a `SecurityAuditor` agent that scans a bead's target files for common security issues and reports findings.

```python
"""SecurityAuditor — custom Gastown agent for security review."""

from __future__ import annotations

from gastown.agents.base import BaseAgent
from gastown.models import Bead, Rig
from gastown.storage import GastownDB

AUDITOR_SYSTEM = """You are a security auditor agent in Gastown.

Your job:
- Review the provided source files for security vulnerabilities
- NEVER modify any files — only report findings

For each issue found, use the report_finding tool.
When you have reviewed all files, call done_signal."""

AUDITOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "report_finding",
            "description": "Report a security finding.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "description": {"type": "string"},
                },
                "required": ["file", "severity", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done_signal",
            "description": "Call when all files have been reviewed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                },
                "required": ["summary"],
            },
        },
    },
]


class SecurityAuditor(BaseAgent):
    def __init__(self, db: GastownDB, rig: Rig) -> None:
        super().__init__(db, rig)
        self._findings: list[dict] = []

    async def audit(self, bead: Bead, file_contents: dict[str, str]) -> list[dict]:
        """Audit the given files and return a list of findings."""
        file_listing = "\n\n".join(
            f"=== {path} ===\n{content}" for path, content in file_contents.items()
        )

        messages = [
            {
                "role": "user",
                "content": (
                    f"Bead: {bead.title}\n\n"
                    f"Review these files for security issues:\n\n{file_listing}"
                ),
            }
        ]

        MAX_ROUNDS = 10
        for _ in range(MAX_ROUNDS):
            response = await self._call_llm(
                messages=messages,
                system=AUDITOR_SYSTEM,
                tools=AUDITOR_TOOLS,
            )

            tool_calls = self._extract_tool_calls(response)

            # Append assistant turn
            choice_msg = response.choices[0].message
            assistant_msg: dict = {"role": "assistant"}
            if choice_msg.content:
                assistant_msg["content"] = choice_msg.content
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

            done = False
            for tc in tool_calls:
                if tc["name"] == "report_finding":
                    self._findings.append(tc["arguments"])
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": "Finding recorded.",
                    })
                elif tc["name"] == "done_signal":
                    done = True
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": "Audit complete.",
                    })

            if done or self._stop_reason(response) == "stop":
                break

        return self._findings
```

---

## Unit and Integration Testing Patterns

### Mocking LLM calls

Use `unittest.mock.AsyncMock` to replace `_call_llm` in unit tests:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from gastown.agents.base import BaseAgent
from gastown.models import Rig
from gastown.storage import GastownDB


@pytest.fixture
async def db(tmp_path):
    db = GastownDB(str(tmp_path / "test.db"))
    await db.initialize()
    return db


@pytest.fixture
def rig(tmp_path):
    return Rig(id="test-rig", name="Test", repo_path=str(tmp_path))


def _make_response(text: str = "", tool_calls: list | None = None):
    """Build a minimal LiteLLM-style ModelResponse mock."""
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = []
    if tool_calls:
        for tc in tool_calls:
            mock_tc = MagicMock()
            mock_tc.id = tc["id"]
            mock_tc.function.name = tc["name"]
            mock_tc.function.arguments = __import__("json").dumps(tc["arguments"])
            msg.tool_calls.append(mock_tc)
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "tool_calls" if tool_calls else "stop"
    response = MagicMock()
    response.choices = [choice]
    return response


@pytest.mark.asyncio
async def test_security_auditor_finds_issue(db, rig):
    from your_module import SecurityAuditor
    from gastown.models import Bead

    auditor = SecurityAuditor(db=db, rig=rig)
    auditor._call_llm = AsyncMock(side_effect=[
        _make_response(tool_calls=[
            {
                "id": "call-1",
                "name": "report_finding",
                "arguments": {
                    "file": "app.py",
                    "severity": "high",
                    "description": "SQL injection risk in query builder",
                },
            }
        ]),
        _make_response(tool_calls=[
            {
                "id": "call-2",
                "name": "done_signal",
                "arguments": {"summary": "1 issue found"},
            }
        ]),
    ])

    bead = Bead(id="gt-test1", rig_id=rig.id, title="Audit", description="")
    findings = await auditor.audit(bead, {"app.py": "query = f'SELECT * FROM {table}'"})

    assert len(findings) == 1
    assert findings[0]["severity"] == "high"
    assert "SQL injection" in findings[0]["description"]
```

### Integration test with a real DB and git repo

```python
@pytest.mark.asyncio
async def test_polecat_commits_work(tmp_path):
    """Full PoleCAT loop with a mock LLM and real git repo."""
    import asyncio
    import subprocess
    from gastown.agents.polecat import PoleCAT
    from gastown.models import Bead, BeadStatus
    from gastown.storage import GastownDB
    from gastown.models import Rig

    # Init a real git repo
    subprocess.run(["git", "init", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit",
                    "--allow-empty", "-m", "init"], check=True)

    db = GastownDB(str(tmp_path / "test.db"))
    await db.initialize()
    rig = Rig(id="r1", name="Test", repo_path=str(tmp_path))
    await db.create_rig(rig)

    bead = Bead(id="gt-zzz99", rig_id=rig.id, title="Create hello.py",
                description="Write a hello.py file")
    bead.worktree_path = str(tmp_path)  # use main dir for simplicity
    await db.create_bead(bead)

    polecat = PoleCAT(db=db, rig=rig)
    polecat._call_llm = AsyncMock(return_value=_make_response(
        tool_calls=[{
            "id": "c1", "name": "write_file",
            "arguments": {"path": "hello.py", "content": "print('hello')"},
        }]
    ))
    # Second call: done_signal
    polecat._call_llm.side_effect = [
        _make_response(tool_calls=[{
            "id": "c1", "name": "write_file",
            "arguments": {"path": "hello.py", "content": "print('hello')"},
        }]),
        _make_response(tool_calls=[{
            "id": "c2", "name": "done_signal",
            "arguments": {"summary": "Created hello.py", "files_changed": ["hello.py"]},
        }]),
    ]

    event_queue = asyncio.Queue()
    result = await polecat.execute(bead, event_queue)

    assert result["status"] == "done"
    assert (tmp_path / "hello.py").exists()
```

---

## Integrating a Custom Agent into the Orchestrator

There are two patterns:

### Pattern 1: Add a new pipeline stage

Modify `GastownOrchestrator.run()` to call your agent before or after an existing stage:

```python
# In orchestrator.py, after step 10 (Refinery) and before step 12 (Mayor review):

auditor = SecurityAuditor(rig=rig, db=self.db)
for bead in merged_beads:
    files = {bead.branch_name: "..."}  # load files from git
    findings = await auditor.audit(bead, files)
    if findings:
        await self.db.log_event(
            "security_audit",
            f"{len(findings)} issues found",
            bead_id=bead.id,
        )
```

### Pattern 2: Replace the PoleCAT

If your custom agent should handle bead execution differently, subclass `PoleCAT` and override `execute()`, then replace the `_run_polecat` closure in the orchestrator.

```python
# Swap in your custom worker
async def _run_polecat(bead: Bead) -> dict:
    async with semaphore:
        worker = MyCustomWorker(db=self.db, rig=rig)
        return await worker.execute(bead, event_queue=event_queue, nudge_queue=nudge_queues.get(bead.id))
```

### Pattern 3: Subclass the orchestrator

Subclass `GastownOrchestrator` and override `run()`:

```python
class AuditingOrchestrator(GastownOrchestrator):
    async def run(self, goal, rig, progress_callback=None):
        result = await super().run(goal, rig, progress_callback)
        # Post-run audit
        auditor = SecurityAuditor(db=self.db, rig=rig)
        # ...
        return result
```

Then pass your subclass to the web app or CLI instead of `GastownOrchestrator`.
