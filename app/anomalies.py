"""
Anomaly detection for GET /stores/{id}/anomalies.

Three anomaly types:
  BILLING_QUEUE_SPIKE  — current queue depth > threshold
  CONVERSION_DROP      — today's conversion rate < 7-day rolling avg * threshold
  DEAD_ZONE            — a named zone has had zero visits in the last 30 minutes

Severity levels: INFO / WARN / CRITICAL
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import func, distinct
from sqlalchemy.orm import Session

from app.database import DBEvent, DBTransaction
from app.models import StoreAnomalies, Anomaly, AnomalySeverity
from app.metrics import _today_start, _compute_conversion

# Thresholds — tune per business requirements
QUEUE_SPIKE_WARN = 5
QUEUE_SPIKE_CRITICAL = 10
CONVERSION_DROP_WARN = 0.20     # 20% below 7-day avg → WARN
CONVERSION_DROP_CRITICAL = 0.40  # 40% below 7-day avg → CRITICAL
DEAD_ZONE_MINUTES = 30


def get_anomalies(store_id: str, db: Session) -> StoreAnomalies:
    now = datetime.now(tz=timezone.utc)
    anomalies: list[Anomaly] = []

    anomalies.extend(_check_queue_spike(store_id, now, db))
    today_rate = _compute_conversion(
        store_id,
        _today_start(),
        db,
    )

    if today_rate < 0.05:

        anomalies.append(
            Anomaly(
                anomaly_type="LOW_CONVERSION",
                severity=AnomalySeverity.WARN,
                description=(
                    f"Conversion rate is only {today_rate:.1%}"
                ),
                suggested_action=(
                    "Review product placement and billing experience."
                ),
                detected_at=now,
            )
        )
    anomalies.extend(_check_dead_zones(store_id, now, db))

    return StoreAnomalies(store_id=store_id, as_of=now, anomalies=anomalies)


# ---------------------------------------------------------------------------
# Individual rule checks
# ---------------------------------------------------------------------------

def _check_queue_spike(
    store_id: str, now: datetime, db: Session
) -> list[Anomaly]:
    five_min_ago = now - timedelta(minutes=5)
    current_depth = (
        db.query(func.max(DBEvent.meta_queue_depth))
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.event_type == "BILLING_QUEUE_JOIN",
            DBEvent.timestamp >= five_min_ago,
            DBEvent.meta_queue_depth.isnot(None),
        )
        .scalar()
        or 0
    )

    if current_depth >= QUEUE_SPIKE_CRITICAL:
        severity = AnomalySeverity.CRITICAL
        action = "Deploy additional billing staff immediately. Consider opening backup counter."
    elif current_depth >= QUEUE_SPIKE_WARN:
        severity = AnomalySeverity.WARN
        action = "Monitor queue — consider calling a second cashier."
    else:
        return []

    return [
        Anomaly(
            anomaly_type="BILLING_QUEUE_SPIKE",
            severity=severity,
            description=f"Billing queue depth is {current_depth} (threshold: {QUEUE_SPIKE_WARN})",
            suggested_action=action,
            detected_at=now,
            zone_id="BILLING",
        )
    ]


def _check_conversion_drop(
    store_id: str, now: datetime, db: Session
) -> list[Anomaly]:
    today_start = _today_start()

    # Today's conversion
    today_rate = _compute_conversion(store_id, today_start, db)

    # 7-day rolling average (exclude today)
    seven_days_ago = today_start - timedelta(days=7)
    daily_rates: list[float] = []
    for day_offset in range(7):
        day_start = seven_days_ago + timedelta(days=day_offset)
        day_end = day_start + timedelta(days=1)
        rate = _compute_conversion_window(store_id, day_start, day_end, db)
        daily_rates.append(rate)

    avg_7d = sum(daily_rates) / len(daily_rates) if daily_rates else 0.0

    if avg_7d == 0.0:
        return []  # Not enough history

    drop_pct = (avg_7d - today_rate) / avg_7d if avg_7d > 0 else 0.0

    if drop_pct >= CONVERSION_DROP_CRITICAL:
        severity = AnomalySeverity.CRITICAL
        action = (
            f"Conversion is {drop_pct:.0%} below 7-day avg ({avg_7d:.1%}). "
            "Escalate to store manager. Check billing queue and staff availability."
        )
    elif drop_pct >= CONVERSION_DROP_WARN:
        severity = AnomalySeverity.WARN
        action = (
            f"Conversion dipped {drop_pct:.0%} below 7-day avg ({avg_7d:.1%}). "
            "Review floor staffing and billing queue depth."
        )
    else:
        return []

    return [
        Anomaly(
            anomaly_type="CONVERSION_DROP",
            severity=severity,
            description=(
                f"Today's conversion rate {today_rate:.1%} vs "
                f"7-day avg {avg_7d:.1%} ({drop_pct:.0%} drop)"
            ),
            suggested_action=action,
            detected_at=now,
        )
    ]


def _check_dead_zones(
    store_id: str, now: datetime, db: Session
) -> list[Anomaly]:
    """Flag zones that had visits earlier today but none in the last 30 min."""
    cutoff = now - timedelta(minutes=DEAD_ZONE_MINUTES)
    today_start = _today_start()

    # Zones active at any point today
    active_zones: set[str] = {
        row[0]
        for row in db.query(distinct(DBEvent.zone_id))
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
            DBEvent.timestamp >= today_start,
            DBEvent.zone_id.isnot(None),
        )
        .all()
    }

    # Zones with a visit in last 30 min
    recently_active: set[str] = {
        row[0]
        for row in db.query(distinct(DBEvent.zone_id))
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
            DBEvent.timestamp >= cutoff,
            DBEvent.zone_id.isnot(None),
        )
        .all()
    }

    dead_zones = active_zones - recently_active
    return [
        Anomaly(
            anomaly_type="DEAD_ZONE",
            severity=AnomalySeverity.INFO,
            description=f"Zone {zone} has had no visits in {DEAD_ZONE_MINUTES} minutes.",
            suggested_action=(
                f"Check zone {zone} — consider repositioning staff or "
                "running a promotion to drive traffic."
            ),
            detected_at=now,
            zone_id=zone,
        )
        for zone in sorted(dead_zones)
    ]


def _compute_conversion_window(
    store_id: str, start: datetime, end: datetime, db: Session
) -> float:
    """Conversion rate for an arbitrary time window (used for 7-day avg)."""
    from datetime import timedelta
    from sqlalchemy import func, distinct as dist

    unique_visitors = (
        db.query(func.count(dist(DBEvent.visitor_id)))
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type == "ENTRY",
            DBEvent.timestamp >= start,
            DBEvent.timestamp < end,
        )
        .scalar()
        or 0
    )
    if unique_visitors == 0:
        return 0.0

    transactions = (
        db.query(DBTransaction.timestamp)
        .filter(
            DBTransaction.store_id == store_id,
            DBTransaction.timestamp >= start,
            DBTransaction.timestamp < end,
        )
        .all()
    )
    window = timedelta(minutes=5)
    converted: set[str] = set()
    for (txn_ts,) in transactions:
        rows = (
            db.query(dist(DBEvent.visitor_id))
            .filter(
                DBEvent.store_id == store_id,
                DBEvent.is_staff == False,
                DBEvent.zone_id.ilike("%billing%"),
                DBEvent.timestamp >= txn_ts - window,
                DBEvent.timestamp <= txn_ts,
            )
            .all()
        )
        converted.update(r[0] for r in rows)

    return min(len(converted) / unique_visitors, 1.0)
