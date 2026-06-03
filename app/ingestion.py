"""
Event ingestion — validates, deduplicates (idempotent by event_id), persists.

Returns per-batch counts: accepted / rejected / duplicate.
Partial success: malformed events are rejected with reasons; valid ones are saved.
"""

from __future__ import annotations

import logging
import structlog
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import DBEvent
from app.models import StoreEvent, IngestResult

log = structlog.get_logger(__name__)


def ingest_events(events: list[StoreEvent], db: Session) -> IngestResult:
    """
    Persist a batch of events.  Idempotent — re-sending the same event_id is
    a no-op (counted as duplicate, not an error).
    """
    accepted = 0
    rejected = 0
    duplicate = 0
    errors: list[dict[str, Any]] = []

    for event in events:
        try:
            db_event = _to_db(event)
            db.add(db_event)
            db.flush()          # surface IntegrityError before commit
            accepted += 1
        except IntegrityError:
            db.rollback()
            duplicate += 1
            log.debug("duplicate_event", event_id=event.event_id)
        except Exception as exc:
            db.rollback()
            rejected += 1
            errors.append({"event_id": event.event_id, "error": str(exc)})
            log.warning("event_rejected", event_id=event.event_id, error=str(exc))

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error("batch_commit_failed", error=str(exc))
        raise

    log.info(
        "ingest_complete",
        accepted=accepted,
        rejected=rejected,
        duplicate=duplicate,
    )
    return IngestResult(
        accepted=accepted,
        rejected=rejected,
        duplicate=duplicate,
        errors=errors,
    )


def _to_db(event: StoreEvent) -> DBEvent:
    return DBEvent(
        event_id=event.event_id,
        store_id=event.store_id,
        camera_id=event.camera_id,
        visitor_id=event.visitor_id,
        event_type=event.event_type.value,
        timestamp=event.timestamp,
        zone_id=event.zone_id,
        dwell_ms=event.dwell_ms,
        is_staff=event.is_staff,
        confidence=event.confidence,
        meta_queue_depth=event.metadata.queue_depth,
        meta_sku_zone=event.metadata.sku_zone,
        meta_session_seq=event.metadata.session_seq,
    )
