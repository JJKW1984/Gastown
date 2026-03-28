"""Unit tests for gastown.storage (GastownDB)."""

import pytest
from gastown.models import Bead, BeadStatus, Convoy, Rig


class TestRigs:
    async def test_create_and_get(self, db, sample_rig):
        await db.create_rig(sample_rig)
        fetched = await db.get_rig(sample_rig.id)
        assert fetched is not None
        assert fetched.id == sample_rig.id
        assert fetched.name == sample_rig.name
        assert fetched.repo_path == sample_rig.repo_path

    async def test_get_nonexistent(self, db):
        assert await db.get_rig("does-not-exist") is None

    async def test_list_rigs(self, db, sample_rig):
        await db.create_rig(sample_rig)
        rigs = await db.list_rigs()
        assert any(r.id == sample_rig.id for r in rigs)

    async def test_upsert(self, db, sample_rig):
        await db.create_rig(sample_rig)
        sample_rig.name = "Updated Name"
        await db.create_rig(sample_rig)  # INSERT OR REPLACE
        fetched = await db.get_rig(sample_rig.id)
        assert fetched.name == "Updated Name"


class TestBeads:
    async def test_create_and_get(self, db, sample_rig, sample_bead):
        await db.create_rig(sample_rig)
        await db.create_bead(sample_bead)
        fetched = await db.get_bead(sample_bead.id)
        assert fetched is not None
        assert fetched.id == sample_bead.id
        assert fetched.title == sample_bead.title
        assert fetched.status == BeadStatus.PENDING

    async def test_get_nonexistent(self, db):
        assert await db.get_bead("gt-zzz99") is None

    async def test_update_status(self, db, sample_rig, sample_bead):
        await db.create_rig(sample_rig)
        await db.create_bead(sample_bead)
        await db.update_bead_status(sample_bead.id, BeadStatus.DONE)
        fetched = await db.get_bead(sample_bead.id)
        assert fetched.status == BeadStatus.DONE

    async def test_update_status_with_kwargs(self, db, sample_rig, sample_bead):
        await db.create_rig(sample_rig)
        await db.create_bead(sample_bead)
        await db.update_bead_status(
            sample_bead.id, BeadStatus.IN_PROGRESS,
            polecat_id="polecat-abc",
            branch_name="bead/gt-abc01",
        )
        fetched = await db.get_bead(sample_bead.id)
        assert fetched.polecat_id == "polecat-abc"
        assert fetched.branch_name == "bead/gt-abc01"

    async def test_update_status_rejects_unknown_column(self, db, sample_rig, sample_bead):
        await db.create_rig(sample_rig)
        await db.create_bead(sample_bead)
        with pytest.raises(ValueError, match="Unknown bead column"):
            await db.update_bead_status(
                sample_bead.id, BeadStatus.IN_PROGRESS,
                drop_table_beads="injected",
            )

    async def test_list_beads_no_filter(self, db, sample_rig, sample_bead):
        await db.create_rig(sample_rig)
        await db.create_bead(sample_bead)
        beads = await db.list_beads(sample_rig.id)
        assert len(beads) == 1
        assert beads[0].id == sample_bead.id

    async def test_list_beads_status_filter(self, db, sample_rig, sample_bead):
        await db.create_rig(sample_rig)
        await db.create_bead(sample_bead)
        # Should find it when filtering by PENDING
        pending = await db.list_beads(sample_rig.id, BeadStatus.PENDING)
        assert len(pending) == 1
        # Should not find it when filtering by DONE
        done = await db.list_beads(sample_rig.id, BeadStatus.DONE)
        assert len(done) == 0

    async def test_metadata_roundtrip(self, db, sample_rig):
        bead = Bead(
            rig_id=sample_rig.id,
            title="t",
            description="d",
            metadata={"key": "value", "num": 42},
        )
        await db.create_rig(sample_rig)
        await db.create_bead(bead)
        fetched = await db.get_bead(bead.id)
        assert fetched.metadata == {"key": "value", "num": 42}


class TestConvoys:
    async def test_create_convoy(self, db, sample_rig):
        await db.create_rig(sample_rig)
        convoy = Convoy(
            id="convoy-test01",
            rig_id=sample_rig.id,
            bead_ids=["gt-abc01", "gt-abc02"],
        )
        await db.create_convoy(convoy)  # Should not raise


class TestEvents:
    async def test_log_and_get(self, db, sample_rig, sample_bead):
        await db.create_rig(sample_rig)
        await db.create_bead(sample_bead)
        await db.log_event("test_event", "details here", bead_id=sample_bead.id)
        events = await db.get_events(sample_bead.id)
        assert len(events) == 1
        assert events[0]["event_type"] == "test_event"
        assert events[0]["details"] == "details here"

    async def test_multiple_events_ordered(self, db, sample_rig, sample_bead):
        await db.create_rig(sample_rig)
        await db.create_bead(sample_bead)
        for i in range(5):
            await db.log_event(f"event_{i}", bead_id=sample_bead.id)
        events = await db.get_events(sample_bead.id)
        assert len(events) == 5
        types = [e["event_type"] for e in events]
        assert types == [f"event_{i}" for i in range(5)]


class TestStatusCounts:
    async def test_counts(self, db, sample_rig):
        await db.create_rig(sample_rig)
        for i in range(3):
            bead = Bead(rig_id=sample_rig.id, title=f"bead {i}", description="d")
            await db.create_bead(bead)
        beads = await db.list_beads(sample_rig.id)
        await db.update_bead_status(beads[0].id, BeadStatus.DONE)
        await db.update_bead_status(beads[1].id, BeadStatus.FAILED)
        counts = await db.get_status_counts(sample_rig.id)
        assert counts["done"] == 1
        assert counts["failed"] == 1
        assert counts["pending"] == 1
