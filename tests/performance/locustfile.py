"""Locust load test for the Gastown REST API.

Usage:
    locust -f tests/performance/locustfile.py --headless -u 10 -r 2 --run-time 30s \\
           --host http://localhost:8000

Or with the web UI:
    locust -f tests/performance/locustfile.py --host http://localhost:8000
"""

from __future__ import annotations

import json
import random
import string
import uuid

from locust import HttpUser, between, task


def _rand_name(n=8) -> str:
    return "".join(random.choices(string.ascii_lowercase, k=n))


class GastownAPIUser(HttpUser):
    """Simulates a user of the Gastown REST API."""

    wait_time = between(0.5, 2.0)

    def on_start(self):
        """Create a rig to work with during the test session."""
        self._rig_id = None
        self._bead_ids: list[str] = []

        resp = self.client.post(
            "/api/rigs",
            json={"path": ".", "name": f"load-test-{_rand_name()}", "description": "locust test"},
        )
        if resp.status_code == 201:
            data = resp.json()
            self._rig_id = data.get("id")

    @task(5)
    def list_rigs(self):
        self.client.get("/api/rigs")

    @task(3)
    def get_rig_status(self):
        if not self._rig_id:
            return
        self.client.get(f"/api/rigs/{self._rig_id}/status")

    @task(3)
    def list_beads(self):
        if not self._rig_id:
            return
        self.client.get(f"/api/rigs/{self._rig_id}/beads")

    @task(1)
    def get_nonexistent_bead(self):
        """Measures 404 response performance."""
        self.client.get(f"/api/beads/gt-zzz00", name="/api/beads/[id]")

    @task(2)
    def get_bead_logs(self):
        if not self._bead_ids:
            return
        bead_id = random.choice(self._bead_ids)
        self.client.get(f"/api/beads/{bead_id}/logs", name="/api/beads/[id]/logs")

    @task(1)
    def get_nonexistent_run(self):
        """Measures 404 response for runs."""
        self.client.get(f"/api/runs/{uuid.uuid4()}", name="/api/runs/[id]")
