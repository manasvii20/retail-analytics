# PROMPT:
#   "Write pytest tests for a FastAPI store metrics endpoint. The function
#    get_metrics() queries SQLite via SQLAlchemy for: unique_visitors (distinct
#    visitor_id on ENTRY events, is_staff=False), conversion_rate (billing zone
#    presence within 5 min of a POS transaction), avg_dwell_ms, queue_depth,
#    abandonment_rate, and per-zone dwell. Include edge cases: empty store,
#    all-staff clip, zero purchases, re-entry (same visitor_id with two ENTRYs).
#    Use an in-memory SQLite fixture. Assert types and boundary values."
#
# CHANGES MADE:
#   - Added explicit timezone handling (all timestamps UTC-aware) — the AI
#     generated naive datetimes which broke the >= comparisons in metrics.py.
#   - Split the all-staff fixture into its own function rather than
#     parametrizing, because parametrize interacted badly with the db fixture
#     scope.
#   - Added the re-entry test: AI only covered new visitors; re-entry
#     dedup is the hardest edge case and the most likely follow-up question.
#   - Removed the AI's assertion `assert metrics.conversion_rate == 1.0`
#     in the single-purchase test — it assumed perfect correlation, but our
#     implementation is time-window based, so I corrected to `> 0`.

import pytest
from datetime import datetime, timezone, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, DBEvent, DBTransaction
from app.metrics import get_metrics


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _now():
    return datetime.now(tz=timezone.utc)


def _entry(visitor_id: str, store_id: str = "STORE_BLR_002", is_staff: bool = False) -> DBEvent:
    return DBEvent(
        event_id=f"evt-entry-{visitor_id}",
        store_id=store_id,
        camera_id="CAM_ENTRY_01",
        visitor_id=visitor_id,
        event_type="ENTRY",
        timestamp=_now(),
        is_staff=is_staff,
        confidence=0.95,
    )


import uuid as _uuid

def _zone_dwell(visitor_id: str, zone_id: str, dwell_ms: int, store_id: str = "STORE_BLR_002") -> DBEvent:
    return DBEvent(
        event_id=f"evt-dwell-{visitor_id}-{zone_id}-{_uuid.uuid4().hex[:8]}",
        store_id=store_id,
        camera_id="CAM_FLOOR_01",
        visitor_id=visitor_id,
        event_type="ZONE_DWELL",
        timestamp=_now(),
        zone_id=zone_id,
        dwell_ms=dwell_ms,
        is_staff=False,
        confidence=0.90,
    )


def _billing_event(visitor_id: str, store_id: str = "STORE_BLR_002") -> DBEvent:
    return DBEvent(
        event_id=f"evt-billing-{visitor_id}",
        store_id=store_id,
        camera_id="CAM_BILLING_01",
        visitor_id=visitor_id,
        event_type="ZONE_ENTER",
        timestamp=_now() - timedelta(minutes=2),
        zone_id="BILLING",
        is_staff=False,
        confidence=0.88,
    )


def _transaction(store_id: str = "STORE_BLR_002") -> DBTransaction:
    return DBTransaction(
        transaction_id=f"txn-{_now().timestamp()}",
        store_id=store_id,
        timestamp=_now(),
        basket_value_inr=1200.0,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEmptyStore:
    """GET /stores/{id}/metrics with zero events."""

    def test_returns_zeros_not_none(self, db):
        result = get_metrics("STORE_BLR_002", db)
        assert result is not None
        assert result.unique_visitors == 0
        assert result.conversion_rate == 0.0
        assert result.queue_depth == 0
        assert result.abandonment_rate == 0.0
        assert result.zone_dwell == []

    def test_correct_store_id_in_response(self, db):
        result = get_metrics("STORE_BLR_002", db)
        assert result.store_id == "STORE_BLR_002"


class TestAllStaffClip:
    """All events are is_staff=True — customer metrics must stay zero."""

    def test_staff_excluded_from_visitor_count(self, db):
        for i in range(5):
            db.add(_entry(f"STAFF_{i:03}", is_staff=True))
        db.commit()

        result = get_metrics("STORE_BLR_002", db)
        assert result.unique_visitors == 0

    def test_staff_excluded_from_conversion(self, db):
        db.add(_entry("STAFF_001", is_staff=True))
        db.add(_transaction())
        db.commit()

        result = get_metrics("STORE_BLR_002", db)
        assert result.conversion_rate == 0.0


class TestZeroPurchases:
    """Visitors present but no POS transactions."""

    def test_conversion_rate_is_zero(self, db):
        for i in range(10):
            db.add(_entry(f"VIS_{i:03}"))
        db.commit()

        result = get_metrics("STORE_BLR_002", db)
        assert result.unique_visitors == 10
        assert result.conversion_rate == 0.0

    def test_abandonment_rate_with_no_queue_joins(self, db):
        db.add(_entry("VIS_001"))
        db.commit()
        result = get_metrics("STORE_BLR_002", db)
        assert result.abandonment_rate == 0.0


class TestReentryDeduplication:
    """
    Same visitor_id appears twice with ENTRY — must be counted as ONE unique visitor.
    This is the re-entry scenario: visitor leaves, comes back, gets a REENTRY event
    from the pipeline, but visitor_id stays the same.
    """

    def test_reentry_counts_as_one_visitor(self, db):
        db.add(DBEvent(
            event_id="evt-entry-VIS_001-first",
            store_id="STORE_BLR_002",
            camera_id="CAM_ENTRY_01",
            visitor_id="VIS_001",
            event_type="ENTRY",
            timestamp=_now() - timedelta(hours=1),
            is_staff=False,
            confidence=0.95,
        ))
        # Re-entry later in the day
        db.add(DBEvent(
            event_id="evt-entry-VIS_001-second",
            store_id="STORE_BLR_002",
            camera_id="CAM_ENTRY_01",
            visitor_id="VIS_001",
            event_type="ENTRY",
            timestamp=_now() - timedelta(minutes=10),
            is_staff=False,
            confidence=0.92,
        ))
        db.commit()

        result = get_metrics("STORE_BLR_002", db)
        assert result.unique_visitors == 1, (
            "Re-entry must not double-count the same visitor_id"
        )


class TestZoneDwell:
    """Per-zone dwell averaging."""

    def test_zone_dwell_computed(self, db):
        db.add(_entry("VIS_001"))
        db.add(_zone_dwell("VIS_001", "SKINCARE", 15_000))
        db.add(_zone_dwell("VIS_001", "SKINCARE", 25_000))
        db.commit()

        result = get_metrics("STORE_BLR_002", db)
        skincare = next((z for z in result.zone_dwell if z.zone_id == "SKINCARE"), None)
        assert skincare is not None
        assert skincare.avg_dwell_ms == pytest.approx(20_000, rel=0.01)
        assert skincare.visit_count == 2

    def test_multiple_zones(self, db):
        db.add(_entry("VIS_001"))
        db.add(_zone_dwell("VIS_001", "SKINCARE", 10_000))
        db.add(_zone_dwell("VIS_001", "HAIRCARE", 5_000))
        db.commit()

        result = get_metrics("STORE_BLR_002", db)
        zone_ids = {z.zone_id for z in result.zone_dwell}
        assert "SKINCARE" in zone_ids
        assert "HAIRCARE" in zone_ids


class TestConversionRate:
    """POS time-window correlation."""

    def test_visitor_in_billing_before_txn_converts(self, db):
        db.add(_entry("VIS_001"))
        db.add(_billing_event("VIS_001"))   # 2 min before _transaction()
        db.add(_transaction())
        db.commit()

        result = get_metrics("STORE_BLR_002", db)
        assert result.conversion_rate > 0

    def test_visitor_not_in_billing_does_not_convert(self, db):
        db.add(_entry("VIS_001"))
        # No billing zone event
        db.add(_transaction())
        db.commit()

        result = get_metrics("STORE_BLR_002", db)
        assert result.conversion_rate == 0.0

    def test_conversion_rate_capped_at_1(self, db):
        """Should never exceed 1.0 even with weird data."""
        for i in range(3):
            db.add(_entry(f"VIS_{i:03}"))
            db.add(_billing_event(f"VIS_{i:03}"))
        db.add(_transaction())
        db.commit()

        result = get_metrics("STORE_BLR_002", db)
        assert result.conversion_rate <= 1.0
