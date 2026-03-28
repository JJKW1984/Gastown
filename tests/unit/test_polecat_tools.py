"""Unit tests for PoleCAT tool execution (filesystem ops, path safety)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gastown.agents.polecat import PoleCAT
from gastown.models import Bead, BeadStatus, Rig


@pytest.fixture
def polecat(db, sample_rig) -> PoleCAT:
    return PoleCAT(db=db, rig=sample_rig)


class TestReadFile:
    async def test_read_existing(self, tmp_path, polecat):
        f = tmp_path / "hello.py"
        f.write_text("print('hello')")
        result = await polecat._execute_tool("read_file", {"path": "hello.py"}, str(tmp_path))
        assert "print('hello')" in result

    async def test_read_missing(self, tmp_path, polecat):
        result = await polecat._execute_tool("read_file", {"path": "missing.py"}, str(tmp_path))
        assert "not found" in result.lower() or "File not found" in result

    async def test_read_truncates_large_file(self, tmp_path, polecat):
        big = tmp_path / "big.txt"
        big.write_text("x" * 100_000)
        result = await polecat._execute_tool("read_file", {"path": "big.txt"}, str(tmp_path))
        assert "truncated" in result


class TestWriteFile:
    async def test_write_new_file(self, tmp_path, polecat):
        result = await polecat._execute_tool(
            "write_file", {"path": "new.py", "content": "x = 1"}, str(tmp_path)
        )
        assert "Written" in result
        assert (tmp_path / "new.py").read_text() == "x = 1"

    async def test_write_creates_parents(self, tmp_path, polecat):
        result = await polecat._execute_tool(
            "write_file", {"path": "a/b/c.py", "content": "pass"}, str(tmp_path)
        )
        assert "Written" in result
        assert (tmp_path / "a" / "b" / "c.py").exists()

    async def test_write_overwrites(self, tmp_path, polecat):
        (tmp_path / "f.py").write_text("old")
        await polecat._execute_tool("write_file", {"path": "f.py", "content": "new"}, str(tmp_path))
        assert (tmp_path / "f.py").read_text() == "new"


class TestPathTraversal:
    async def test_blocks_traversal_read(self, tmp_path, polecat):
        with pytest.raises(ValueError, match="traversal"):
            polecat._safe_path("../../etc/passwd", str(tmp_path))

    async def test_blocks_traversal_write(self, tmp_path, polecat):
        with pytest.raises(ValueError, match="traversal"):
            polecat._safe_path("../outside.txt", str(tmp_path))

    async def test_allows_subdirectory(self, tmp_path, polecat):
        path = polecat._safe_path("sub/file.py", str(tmp_path))
        assert str(path).startswith(str(tmp_path.resolve()))


class TestListDirectory:
    async def test_lists_files(self, tmp_path, polecat):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        result = await polecat._execute_tool("list_directory", {"path": "."}, str(tmp_path))
        assert "a.py" in result
        assert "b.py" in result

    async def test_shows_dir_marker(self, tmp_path, polecat):
        (tmp_path / "subdir").mkdir()
        result = await polecat._execute_tool("list_directory", {"path": "."}, str(tmp_path))
        assert "[dir]" in result

    async def test_missing_dir(self, tmp_path, polecat):
        result = await polecat._execute_tool("list_directory", {"path": "nonexistent"}, str(tmp_path))
        assert "not found" in result.lower() or "Directory not found" in result


class TestRunCommand:
    async def test_basic_command(self, tmp_path, polecat):
        result = await polecat._execute_tool(
            "run_command", {"command": "echo hello"}, str(tmp_path)
        )
        assert "hello" in result

    async def test_exit_code_shown_on_failure(self, tmp_path, polecat):
        result = await polecat._execute_tool(
            "run_command", {"command": "exit 42"}, str(tmp_path)
        )
        assert "42" in result

    async def test_timeout(self, tmp_path, polecat, monkeypatch):
        # Patch timeout to 1 second for test speed
        import gastown.agents.polecat as pc_mod
        monkeypatch.setattr(pc_mod, "COMMAND_TIMEOUT", 1)
        result = await polecat._execute_tool(
            "run_command", {"command": "ping -n 10 127.0.0.1"}, str(tmp_path)
        )
        assert "timeout" in result.lower()
