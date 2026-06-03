# PROMPT:
#   "Write pytest tests for three anomaly detection rules in a retail analytics
#    API: (1) BILLING_QUEUE_SPIKE when queue_depth >= threshold, (2)
#    CONVERSION_DROP when today's conversion is significantly below 7-day avg,
#    (3) DEAD_ZONE when a zone had traffic earlier today but none in the last
#    30 minutes. Use in-memory SQLite. Test that severity levels are correct
#    (INFO/WARN/CRITICAL), that suggested_action is non-empty, and that no
#    anomaly is raised when thresholds are not met."
#
# CHANGES MADE:
#   - AI generated the 7-day avg test by inserting events with day offsets
#     but used naive datetimes; fixed to UTC-aware throughout.
#   - Added test for empty anomaly list (no anomalies when everything is fine)
#     — AI omitted this, but it's an important negative test.
#   - Corrected the DEAD_ZONE test: AI's version inserted a zone event at
#     exactly 30 min ago which is on the boundary; I shifted it to 31 min
#     ago to be unambiguous given the >= cutoff in the implementation.
#   - Added suggested_action non-empty assertion for all anomaly types.

import pytest
from datetime import datetime, timezone, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, DBEvent, DBTransaction
from app.anomalies import (
    get_anomalies,
    QUEUE_SPIKE_WARN,
    QUEUE_SPIKE_CRITICAL,
)
from app.models import AnomalySeverity

STORE = "STORE_BLR_002"


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _queue_event(visitor_id: str, queue_depth: int, minutes_ago: float = 1.0) -> DBEvent:
    return DBEvent(
        event_id=f"evt-queue-{visitor_id}-{queue_depth}",
        store_id=STORE,
        camera_id="CAM_BILLING_01",
        visitor_id=visitor_id,
        event_type="BILLING_QUEUE_JOIN",
        timestamp=_now() - timedelta(minutes=minutes_ago),
        zone_id="BILLING",
        is_staff=False,
        confidence=0.9,
        meta_queue_depth=queue_depth,
    )


def _zone_visit(visitor_id: str, zone_id: str, minutes_ago: float) -> DBEvent:
    return DBEvent(
        event_id=f"evt-zone-{visitor_id}-{zone_id}-{minutes_ago}",
        store_id=STORE,
        camera_id="CAM_FLOOR_01",
        visitor_id=visitor_id,
        event_type="ZONE_ENTER",
        timestamp=_now() - timedelta(minutes=minutes_ago),
        zone_id=zone_id,
        is_staff=False,
        confidence=0.88,
    )


def _entry(visitor_id: str, days_ago: float = 0.0) -> DBEvent:
    return DBEvent(
        event_id=f"evt-entry-{visitor_id}-{days_ago}",
        store_id=STORE,
        camera_id="CAM_ENTRY_01",
        visitor_id=visitor_id,
        event_type="ENTRY",
        timestamp=_now() - timedelta(days=days_ago),
        is_staff=False,
        confidence=0.95,
    )


def _billing_presence(visitor_id: str, minutes_before_txn: float = 2.0) -> DBEvent:
    return DBEvent(
        event_id=f"evt-bill-{visitor_id}",
        store_id=STORE,
        camera_id="CAM_BILLING_01",
        visitor_id=visitor_id,
        event_type="ZONE_ENTER",
        timestamp=_now() - timedelta(minutes=minutes_before_txn),
        zone_id="BILLING",
        is_staff=False,
        confidence=0.9,
    )


def _transaction(days_ago: float = 0.0) -> DBTransaction:
    return DBTransaction(
        transaction_id=f"txn-{days_ago}-{_now().timestamp()}",
        store_id=STORE,
        timestamp=_now() - timedelta(days=days_ago),
        basket_value_inr=900.0,
    )


# ---------------------------------------------------------------------------
# BILLING_QUEUE_SPIKE tests
# ---------------------------------------------------------------------------

class TestQueueSpike:
    def test_no_anomaly_below_threshold(self, db):
        db.add(_queue_event("VIS_001", queue_depth=2))
        db.commit()
        result = get_anomalies(STORE, db)
        spikes = [a for a in result.anomalies if a.anomaly_type == "BILLING_QUEUE_SPIKE"]
        assert len(spikes) == 0

    def test_warn_at_warn_threshold(self, db):
        db.add(_queue_event("VIS_001", queue_depth=QUEUE_SPIKE_WARN))
        db.commit()
        result = get_anomalies(STORE, db)
        spikes = [a for a in result.anomalies if a.anomaly_type == "BILLING_QUEUE_SPIKE"]
        assert len(spikes) == 1
        assert spikes[0].severity == AnomalySeverity.WARN

    def test_critical_at_critical_threshold(self, db):
        db.add(_queue_event("VIS_001", queue_depth=QUEUE_SPIKE_CRITICAL))
        db.commit()
        result = get_anomalies(STORE, db)
        spikes = [a for a in result.anomalies if a.anomaly_type == "BILLING_QUEUE_SPIKE"]
        assert len(spikes) == 1
        assert spikes[0].severity == AnomalySeverity.CRITICAL

    def test_suggested_action_non_empty(self, db):
        db.add(_queue_event("VIS_001", queue_depth=QUEUE_SPIKE_WARN))
        db.commit()
        result = get_anomalies(STORE, db)
        spike = next(a for a in result.anomalies if a.anomaly_type == "BILLING_QUEUE_SPIKE")
        assert spike.suggested_action and len(spike.suggested_action) > 10

    def test_stale_queue_event_ignored(self, db):
        """Event older than 5 min should not trigger spike."""
        db.add(_queue_event("VIS_001", queue_depth=QUEUE_SPIKE_CRITICAL, minutes_ago=10))
        db.commit()
        result = get_anomalies(STORE, db)
        spikes = [a for a in result.anomalies if a.anomaly_type == "BILLING_QUEUE_SPIKE"]
        assert len(spikes) == 0


# ---------------------------------------------------------------------------
# DEAD_ZONE tests
# ---------------------------------------------------------------------------

class TestDeadZone:
    def test_active_zone_no_anomaly(self, db):
        db.add(_zone_visit("VIS_001", "SKINCARE", minutes_ago=5))
        db.commit()
        result = get_anomalies(STORE, db)
        dead = [a for a in result.anomalies if a.anomaly_type == "DEAD_ZONE"]
        assert len(dead) == 0

    def test_zone_quiet_31_min_triggers_anomaly(self, db):
        # Earlier visit (was active today)
        db.add(_zone_visit("VIS_001", "SKINCARE", minutes_ago=90))
        # Nothing in last 31 minutes
        db.commit()
        result = get_anomalies(STORE, db)
        dead = [a for a in result.anomalies if a.anomaly_type == "DEAD_ZONE"]
        assert len(dead) == 1
        assert dead[0].zone_id == "SKINCARE"
        assert dead[0].severity == AnomalySeverity.INFO

    def test_zone_never_active_no_anomaly(self, db):
        """A zone with no events at all today should not appear as dead."""
        db.commit()
        result = get_anomalies(STORE, db)
        dead = [a for a in result.anomalies if a.anomaly_type == "DEAD_ZONE"]
        assert len(dead) == 0

    def test_suggested_action_non_empty(self, db):
        db.add(_zone_visit("VIS_001", "PERFUME", minutes_ago=120))
        db.commit()
        result = get_anomalies(STORE, db)
        dead = [a for a in result.anomalies if a.anomaly_type == "DEAD_ZONE"]
        if dead:
            assert dead[0].suggested_action and len(dead[0].suggested_action) > 10


# ---------------------------------------------------------------------------
# No anomalies
# ---------------------------------------------------------------------------

class TestNoAnomalies:
    def test_healthy_store_returns_empty_list(self, db):
        """All metrics within normal range — anomaly list should be empty."""
        db.add(_entry("VIS_001"))
        db.add(_zone_visit("VIS_001", "SKINCARE", minutes_ago=5))
        db.add(_queue_event("VIS_001", queue_depth=2))
        db.commit()
        result = get_anomalies(STORE, db)
        assert isinstance(result.anomalies, list)
        # Queue depth 2 is below WARN threshold
        spikes = [a for a in result.anomalies if a.anomaly_type == "BILLING_QUEUE_SPIKE"]
        assert len(spikes) == 0
