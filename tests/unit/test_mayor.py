"""Unit tests for the Mayor agent (mocked LLM calls)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from gastown.agents.mayor import Mayor
from gastown.models import BeadStatus, DecompositionResult


MOCK_DECOMPOSITION = {
    "beads": [
        {
            "title": "Create hello function",
            "description": "Add hello(name) to hello.py",
            "priority": 0,
            "estimated_files": ["hello.py"],
            "depends_on": [],
        },
        {
            "title": "Write tests",
            "description": "Add tests for hello() in test_hello.py",
            "priority": 1,
            "estimated_files": ["test_hello.py"],
            "depends_on": ["Create hello function"],
        },
    ],
    "summary": "Split into implementation + tests",
}


class TestMayorDecompose:
    async def test_returns_beads(self, db, sample_rig):
        await db.create_rig(sample_rig)
        mayor = Mayor(db=db, rig=sample_rig)

        # Build mock tool-call response
        from tests.conftest import MockLLMResponse, MockToolCall
        mock_response = MockLLMResponse(
            tool_calls=[
                MockToolCall("tc1", "decompose_goal", json.dumps(MOCK_DECOMPOSITION))
            ],
            finish_reason="tool_calls",
        )

        with patch.object(mayor, "_call_llm", new=AsyncMock(return_value=mock_response)):
            beads = await mayor.decompose("add hello function", sample_rig)

        assert len(beads) == 2
        assert beads[0].title == "Create hello function"
        assert beads[1].title == "Write tests"
        assert all(b.rig_id == sample_rig.id for b in beads)

    async def test_beads_persisted_to_db(self, db, sample_rig):
        await db.create_rig(sample_rig)
        mayor = Mayor(db=db, rig=sample_rig)

        from tests.conftest import MockLLMResponse, MockToolCall
        mock_response = MockLLMResponse(
            tool_calls=[MockToolCall("tc1", "decompose_goal", json.dumps(MOCK_DECOMPOSITION))],
            finish_reason="tool_calls",
        )

        with patch.object(mayor, "_call_llm", new=AsyncMock(return_value=mock_response)):
            beads = await mayor.decompose("add hello function", sample_rig)

        for bead in beads:
            stored = await db.get_bead(bead.id)
            assert stored is not None
            assert stored.title == bead.title

    async def test_fallback_text_parsing(self, db, sample_rig):
        """Mayor should parse raw JSON text if no tool call is returned."""
        await db.create_rig(sample_rig)
        mayor = Mayor(db=db, rig=sample_rig)

        from tests.conftest import MockLLMResponse
        mock_response = MockLLMResponse(content=json.dumps(MOCK_DECOMPOSITION))

        with patch.object(mayor, "_call_llm", new=AsyncMock(return_value=mock_response)):
            beads = await mayor.decompose("add hello function", sample_rig)

        assert len(beads) == 2


class TestMayorSling:
    async def test_creates_convoy(self, db, sample_rig, sample_bead):
        await db.create_rig(sample_rig)
        await db.create_bead(sample_bead)
        mayor = Mayor(db=db, rig=sample_rig)

        convoy = await mayor.sling([sample_bead], sample_rig)

        assert convoy.rig_id == sample_rig.id
        assert sample_bead.id in convoy.bead_ids
        updated = await db.get_bead(sample_bead.id)
        assert updated.status == BeadStatus.IN_PROGRESS

    async def test_convoy_id_on_bead(self, db, sample_rig, sample_bead):
        await db.create_rig(sample_rig)
        await db.create_bead(sample_bead)
        mayor = Mayor(db=db, rig=sample_rig)

        convoy = await mayor.sling([sample_bead], sample_rig)
        updated = await db.get_bead(sample_bead.id)
        assert updated.convoy_id == convoy.id


class TestMayorReview:
    async def test_review_empty(self, db, sample_rig):
        mayor = Mayor(db=db, rig=sample_rig)
        result = await mayor.review_results([])
        assert "no beads" in result.lower()

    async def test_review_with_beads(self, db, sample_rig, sample_bead):
        mayor = Mayor(db=db, rig=sample_rig)
        from tests.conftest import MockLLMResponse
        mock_response = MockLLMResponse(content="Great work, everything looks good.")

        with patch.object(mayor, "_call_llm", new=AsyncMock(return_value=mock_response)):
            review = await mayor.review_results([sample_bead])

        assert "Great work" in review
