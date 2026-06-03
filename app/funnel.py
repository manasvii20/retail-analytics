"""
Conversion funnel: Entry → Zone Visit → Billing Queue → Purchase

Session is the unit. Re-entries must NOT double-count a visitor.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import distinct
from sqlalchemy.orm import Session

from app.database import DBEvent
from app.models import StoreFunnel, FunnelStage
from app.metrics import _today_start


def get_funnel(store_id: str, db: Session) -> Optional[StoreFunnel]:
    since = _today_start()

    # Stage 1 — unique entries
    entry_visitors: set[str] = {
        row[0]
        for row in db.query(distinct(DBEvent.visitor_id))
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type == "ENTRY",
            DBEvent.timestamp >= since,
        )
        .all()
    }

    total_entries = len(entry_visitors)

    # Stage 2 — zone visit
    zone_visitors: set[str] = {
        row[0]
        for row in db.query(distinct(DBEvent.visitor_id))
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
            DBEvent.timestamp >= since,
            DBEvent.visitor_id.in_(entry_visitors),
        )
        .all()
    }

    zone_visit_count = len(zone_visitors)

    # Stage 3 — billing queue
    billing_visitors: set[str] = {
        row[0]
        for row in db.query(distinct(DBEvent.visitor_id))
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type == "BILLING_QUEUE_JOIN",
            DBEvent.timestamp >= since,
            DBEvent.visitor_id.in_(entry_visitors),
        )
        .all()
    }

    billing_count = len(billing_visitors)

    # Stage 4 — purchase
    purchase_visitors: set[str] = {
        row[0]
        for row in db.query(distinct(DBEvent.visitor_id))
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type == "PURCHASE",
            DBEvent.timestamp >= since,
            DBEvent.visitor_id.in_(entry_visitors),
        )
        .all()
    }

    purchase_count = len(purchase_visitors)

    def drop_off(current: int, previous: int) -> float:
        if previous == 0:
            return 0.0

        return round((1 - current / previous) * 100, 2)

    stages = [
        FunnelStage(
            stage="ENTRY",
            count=total_entries,
            drop_off_pct=0.0,
        ),
        FunnelStage(
            stage="ZONE_VISIT",
            count=zone_visit_count,
            drop_off_pct=drop_off(
                zone_visit_count,
                total_entries,
            ),
        ),
        FunnelStage(
            stage="BILLING_QUEUE",
            count=billing_count,
            drop_off_pct=drop_off(
                billing_count,
                zone_visit_count,
            ),
        ),
        FunnelStage(
            stage="PURCHASE",
            count=purchase_count,
            drop_off_pct=drop_off(
                purchase_count,
                billing_count,
            ),
        ),
    ]

    return StoreFunnel(
        store_id=store_id,
        as_of=datetime.now(tz=timezone.utc),
        stages=stages,
    )