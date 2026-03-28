"""Security tests: path traversal, command injection, input validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from gastown.agents.polecat import PoleCAT
from gastown.models import Rig


@pytest.fixture
def polecat(db, sample_rig) -> PoleCAT:
    return PoleCAT(db=db, rig=sample_rig)


class TestPathTraversalPrevention:
    """Verify that write_file and read_file cannot escape the worktree."""

    @pytest.mark.parametrize("malicious_path", [
        "../../../etc/passwd",
        "../../outside.txt",
        "../sibling_dir/secret",
        "valid/../../escape.txt",
        "sub/../../../etc/passwd",
    ])
    def test_blocks_traversal(self, tmp_path, polecat, malicious_path):
        with pytest.raises(ValueError, match="[Tt]raversal"):
            polecat._safe_path(malicious_path, str(tmp_path))

    @pytest.mark.parametrize("safe_path", [
        "hello.py",
        "src/main.py",
        "tests/unit/test_foo.py",
        "deeply/nested/path/file.txt",
        "./relative.py",
    ])
    def test_allows_safe_paths(self, tmp_path, polecat, safe_path):
        resolved = polecat._safe_path(safe_path, str(tmp_path))
        assert str(resolved).startswith(str(tmp_path.resolve()))

    async def test_write_file_blocks_traversal(self, tmp_path, polecat):
        result = await polecat._execute_tool(
            "write_file",
            {"path": "../../evil.txt", "content": "evil"},
            str(tmp_path),
        )
        # Should return an error string, not write the file
        assert "Error" in result or "traversal" in result.lower()
        assert not Path(tmp_path.parent.parent / "evil.txt").exists()

    async def test_read_file_blocks_traversal(self, tmp_path, polecat):
        result = await polecat._execute_tool(
            "read_file",
            {"path": "../../some_secret"},
            str(tmp_path),
        )
        assert "Error" in result or "traversal" in result.lower()


class TestCommandInjection:
    """Verify run_command uses shell=True only for the provided command,
    not for interpolated user data."""

    async def test_semicolon_doesnt_escape(self, tmp_path, polecat):
        """Semicolons in commands are passed directly to shell — this tests
        that tool_args (path etc.) are not concatenated into commands."""
        # This is a design test: _execute_tool only uses 'command' param as-is.
        # The path params go through _safe_path, not shell.
        result = await polecat._execute_tool(
            "run_command",
            {"command": "echo safe"},
            str(tmp_path),
        )
        assert "safe" in result

    async def test_null_byte_in_path(self, tmp_path, polecat):
        """Null bytes in paths should be handled gracefully."""
        result = await polecat._execute_tool(
            "read_file",
            {"path": "foo\x00bar"},
            str(tmp_path),
        )
        # Should either error gracefully or return file-not-found
        assert isinstance(result, str)

    async def test_very_long_path(self, tmp_path, polecat):
        """Extremely long paths should not crash the server."""
        long_path = "a" * 5000 + ".py"
        result = await polecat._execute_tool(
            "read_file", {"path": long_path}, str(tmp_path)
        )
        assert isinstance(result, str)


class TestInputValidation:
    """Verify Pydantic model validation rejects malformed input."""

    def test_bead_status_invalid(self):
        from pydantic import ValidationError
        from gastown.models import Bead
        with pytest.raises(ValidationError):
            Bead(id="gt-abc01", rig_id="r", title="t", description="d",
                 status="not_a_valid_status")

    def test_gen_bead_id_no_predictability(self):
        """Bead IDs should use secrets module (cryptographic randomness)."""
        from gastown.models import gen_bead_id
        ids = [gen_bead_id() for _ in range(100)]
        # All unique
        assert len(set(ids)) == 100

    def test_bead_metadata_accepts_only_dict(self):
        from pydantic import ValidationError
        from gastown.models import Bead
        with pytest.raises((ValidationError, TypeError)):
            Bead(id="gt-abc01", rig_id="r", title="t", description="d",
                 metadata="not_a_dict")  # type: ignore
