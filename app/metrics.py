"""
Real-time metrics computation for GET /stores/{id}/metrics.

All queries filter is_staff=False.  Conversion is computed by correlating
visitor billing-zone presence with POS transactions in a 5-minute window.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import func, distinct, and_, text
from sqlalchemy.orm import Session

from app.database import DBEvent, DBTransaction
from app.models import StoreMetrics, ZoneDwell


# Window used for "today" metrics — last 24 h rolling
WINDOW_HOURS = 24
BILLING_ZONE_KEYWORDS = {"BILLING", "CHECKOUT", "COUNTER", "QUEUE"}
CONVERSION_WINDOW_MINUTES = 5


def _today_start() -> datetime:
    now = datetime.now(tz=timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def get_metrics(store_id: str, db: Session) -> Optional[StoreMetrics]:
    since = _today_start()

    # --- unique customers (non-staff ENTRY events) ---
    unique_visitors = (
        db.query(func.count(distinct(DBEvent.visitor_id)))
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type == "ENTRY",
            DBEvent.timestamp >= since,
        )
        .scalar()
        or 0
    )

    # --- current queue depth (max queue_depth seen in last 5 min) ---
    five_min_ago = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
    queue_depth = (
        db.query(func.max(DBEvent.meta_queue_depth))
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type == "BILLING_QUEUE_JOIN",
            DBEvent.timestamp >= five_min_ago,
            DBEvent.meta_queue_depth.isnot(None),
        )
        .scalar()
        or 0
    )

    # --- abandonment rate ---
    queue_joins = (
        db.query(func.count())
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type == "BILLING_QUEUE_JOIN",
            DBEvent.timestamp >= since,
        )
        .scalar()
        or 0
    )
    queue_abandons = (
        db.query(func.count())
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type == "BILLING_QUEUE_ABANDON",
            DBEvent.timestamp >= since,
        )
        .scalar()
        or 0
    )
    abandonment_rate = (queue_abandons / queue_joins) if queue_joins > 0 else 0.0

    # --- average dwell across all zone events ---
    avg_dwell = (
        db.query(func.avg(DBEvent.dwell_ms))
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type.in_(["ZONE_DWELL", "ZONE_EXIT"]),
            DBEvent.timestamp >= since,
        )
        .scalar()
        or 0.0
    )

    # --- per-zone dwell ---
    zone_rows = (
        db.query(
            DBEvent.zone_id,
            func.avg(DBEvent.dwell_ms).label("avg_dwell"),
            func.count().label("visits"),
        )
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type.in_(["ZONE_DWELL", "ZONE_EXIT"]),
            DBEvent.timestamp >= since,
            DBEvent.zone_id.isnot(None),
        )
        .group_by(DBEvent.zone_id)
        .all()
    )
    zone_dwell = [
        ZoneDwell(zone_id=r.zone_id, avg_dwell_ms=r.avg_dwell or 0.0, visit_count=r.visits)
        for r in zone_rows
    ]

    # --- conversion rate via POS time-window correlation ---
    conversion_rate = _compute_conversion(store_id, since, db)

    return StoreMetrics(
        store_id=store_id,
        as_of=datetime.now(tz=timezone.utc),
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        avg_dwell_ms=float(avg_dwell),
        queue_depth=int(queue_depth),
        abandonment_rate=abandonment_rate,
        zone_dwell=zone_dwell,
    )


def _compute_conversion(store_id: str, since: datetime, db: Session) -> float:
    """
    A visitor session converts if the visitor was in the billing zone within
    CONVERSION_WINDOW_MINUTES before a POS transaction timestamp.

    Approach:
      1. Get all POS transactions for the store today.
      2. For each transaction, find visitor_ids in the billing zone in the
         window [txn_ts - 5min, txn_ts].
      3. Union of those visitor_ids = converted set.
      4. Conversion rate = |converted| / |unique_visitors|.
    """
    transactions = (
        db.query(DBTransaction.timestamp)
        .filter(
            DBTransaction.store_id == store_id,
            DBTransaction.timestamp >= since,
        )
        .all()
    )

    if not transactions:
        return 0.0

    converted_visitors: set[str] = set()
    window = timedelta(minutes=CONVERSION_WINDOW_MINUTES)

    for (txn_ts,) in transactions:
        rows = (
            db.query(distinct(DBEvent.visitor_id))
            .filter(
                DBEvent.store_id == store_id,
                DBEvent.is_staff == False,
                DBEvent.event_type.in_(
                    ["BILLING_QUEUE_JOIN", "ZONE_ENTER", "ZONE_DWELL"]
                ),
                DBEvent.zone_id.ilike("%billing%"),
                DBEvent.timestamp >= txn_ts - window,
                DBEvent.timestamp <= txn_ts,
            )
            .all()
        )
        converted_visitors.update(r[0] for r in rows)

    unique_visitors = (
        db.query(func.count(distinct(DBEvent.visitor_id)))
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type == "ENTRY",
            DBEvent.timestamp >= since,
        )
        .scalar()
        or 0
    )

    if unique_visitors == 0:
        return 0.0

    return min(len(converted_visitors) / unique_visitors, 1.0)
