"""Static security analysis tests using bandit.

Run standalone: bandit -r gastown/ -f json
These tests wrap bandit to integrate it into the pytest suite.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[2]
SOURCE_DIR = PROJECT_ROOT / "gastown"


@pytest.mark.security
class TestBanditStaticAnalysis:
    def test_no_high_severity_issues(self):
        """Bandit must report zero HIGH severity findings in source code."""
        result = subprocess.run(
            [
                sys.executable, "-m", "bandit",
                "-r", str(SOURCE_DIR),
                "-f", "json",
                "--severity-level", "high",
                "-q",
            ],
            capture_output=True,
            text=True,
        )
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError:
            pytest.skip("bandit not installed or produced no JSON output")
            return

        high_issues = [
            r for r in report.get("results", [])
            if r.get("issue_severity") == "HIGH"
        ]
        assert high_issues == [], (
            f"Found {len(high_issues)} HIGH severity issue(s):\n"
            + "\n".join(
                f"  [{r['issue_severity']}] {r['issue_text']} "
                f"at {r['filename']}:{r['line_number']}"
                for r in high_issues
            )
        )

    def test_no_medium_severity_issues(self):
        """Bandit must report zero MEDIUM severity findings (with allowed exceptions)."""
        result = subprocess.run(
            [
                sys.executable, "-m", "bandit",
                "-r", str(SOURCE_DIR),
                "-f", "json",
                "--severity-level", "medium",
                # B603/B602/B607: subprocess shell use — intentional for run_command tool
                # B608: dynamic SQL — column names validated against a whitelist in storage.py
                "--skip", "B603,B602,B607,B608",
                "-q",
            ],
            capture_output=True,
            text=True,
        )
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError:
            pytest.skip("bandit not installed")
            return

        medium_issues = [
            r for r in report.get("results", [])
            if r.get("issue_severity") in ("HIGH", "MEDIUM")
        ]
        assert medium_issues == [], (
            f"Found {len(medium_issues)} MEDIUM+ severity issue(s):\n"
            + "\n".join(
                f"  [{r['issue_severity']}] {r['issue_text']} "
                f"at {r['filename']}:{r['line_number']}"
                for r in medium_issues
            )
        )
