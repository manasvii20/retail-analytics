"""
Zone heatmap — visit frequency + avg dwell, normalised 0-100.

Sets data_confidence=False when fewer than 20 unique sessions have been
recorded for the store today.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, distinct
from sqlalchemy.orm import Session

from app.database import DBEvent
from app.models import StoreHeatmap, HeatmapZone
from app.metrics import _today_start

MIN_SESSIONS_FOR_CONFIDENCE = 20


def get_heatmap(store_id: str, db: Session) -> Optional[StoreHeatmap]:
    since = _today_start()

    # Count unique sessions to set data_confidence
    unique_sessions = (
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
    data_confidence = unique_sessions >= MIN_SESSIONS_FOR_CONFIDENCE

    # Per-zone: visit count + avg dwell
    rows = (
        db.query(
            DBEvent.zone_id,
            func.count(distinct(DBEvent.visitor_id)).label("visit_count"),
            func.avg(DBEvent.dwell_ms).label("avg_dwell"),
        )
        .filter(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type.in_(["ZONE_ENTER", "ZONE_DWELL", "ZONE_EXIT"]),
            DBEvent.timestamp >= since,
            DBEvent.zone_id.isnot(None),
        )
        .group_by(DBEvent.zone_id)
        .all()
    )

    if not rows:
        return StoreHeatmap(
            store_id=store_id,
            as_of=datetime.now(tz=timezone.utc),
            zones=[],
            data_confidence=data_confidence,
        )

    # Normalise visit_count to 0-100
    max_visits = max(r.visit_count for r in rows) or 1

    zones = [
        HeatmapZone(
            zone_id=r.zone_id,
            visit_frequency=r.visit_count,
            avg_dwell_ms=float(r.avg_dwell or 0.0),
            normalised_score=round((r.visit_count / max_visits) * 100, 2),
        )
        for r in rows
    ]

    # Sort descending by normalised score
    zones.sort(key=lambda z: z.normalised_score, reverse=True)

    return StoreHeatmap(
        store_id=store_id,
        as_of=datetime.now(tz=timezone.utc),
        zones=zones,
        data_confidence=data_confidence,
    )
