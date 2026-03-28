"""Shared pytest fixtures for all Gastown tests."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import AsyncGenerator

import pytest

from gastown.models import Bead, BeadStatus, Rig
from gastown.storage import GastownDB


# ---------------------------------------------------------------------------
# Event loop — single loop for all async tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


# ---------------------------------------------------------------------------
# Temporary git repository
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Create a minimal git repository with an initial commit."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@gastown.test"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Gastown Test"],
        cwd=repo, check=True, capture_output=True,
    )
    (repo / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo, check=True, capture_output=True,
    )
    return repo


# ---------------------------------------------------------------------------
# In-memory database
# ---------------------------------------------------------------------------

@pytest.fixture
async def db(tmp_path: Path) -> AsyncGenerator[GastownDB, None]:
    """Provide a fresh in-memory GastownDB for each test."""
    db_path = str(tmp_path / "test.db")
    database = GastownDB(db_path)
    await database.initialize()
    yield database
    database.close()


# ---------------------------------------------------------------------------
# Sample Rig
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_rig(tmp_repo: Path) -> Rig:
    return Rig(id="test-rig", name="Test Rig", repo_path=str(tmp_repo))


# ---------------------------------------------------------------------------
# Sample Bead
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_bead(sample_rig: Rig) -> Bead:
    return Bead(
        id="gt-abc01",
        rig_id=sample_rig.id,
        title="Add hello function",
        description="Create a hello(name) function in hello.py that returns 'Hello, {name}!'",
        priority=0,
    )


# ---------------------------------------------------------------------------
# LLM mock helpers — patch litellm.acompletion to avoid real API calls
# ---------------------------------------------------------------------------

class MockChoice:
    def __init__(self, content: str, tool_calls=None, finish_reason: str = "stop"):
        self.finish_reason = finish_reason
        self.message = MockMessage(content, tool_calls)


class MockMessage:
    def __init__(self, content: str, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class MockLLMResponse:
    def __init__(self, content: str = "", tool_calls=None, finish_reason: str = "stop"):
        self.choices = [MockChoice(content, tool_calls, finish_reason)]


class MockToolCall:
    def __init__(self, call_id: str, name: str, arguments: str):
        self.id = call_id
        self.function = MockFunction(name, arguments)


class MockFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments
