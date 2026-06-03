"""
Store Intelligence API — FastAPI entrypoint.

Routes:
  POST /events/ingest
  GET  /stores/{store_id}/metrics
  GET  /stores/{store_id}/funnel
  GET  /stores/{store_id}/heatmap
  GET  /stores/{store_id}/anomalies
  GET  /health
"""

from __future__ import annotations

import structlog
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import create_tables, get_db
from app.logging_config import RequestLoggingMiddleware
from app.models import (
    IngestRequest,
    IngestResult,
    StoreMetrics,
    StoreFunnel,
    StoreHeatmap,
    StoreAnomalies,
    HealthResponse,
)
from app.ingestion import ingest_events
from app.metrics import get_metrics
from app.funnel import get_funnel
from app.heatmap import get_heatmap
from app.anomalies import get_anomalies
from app.health import get_health

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", msg="Creating database tables")
    create_tables()
    yield
    log.info("shutdown", msg="API shutting down")


app = FastAPI(
    title="Store Intelligence API",
    description="Real-time offline store analytics for Apex Retail",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(RequestLoggingMiddleware)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.exception_handler(OperationalError)
async def db_error_handler(request, exc):
    log.error("db_unavailable", error=str(exc))
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error": "database_unavailable",
            "message": "The database is temporarily unavailable. Please retry shortly.",
        },
    )


@app.exception_handler(Exception)
async def generic_error_handler(request, exc):
    log.error("unhandled_exception", error=str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_error",
            "message": "An unexpected error occurred.",
        },
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post(
    "/events/ingest",
    response_model=IngestResult,
    status_code=status.HTTP_200_OK,
    summary="Ingest a batch of up to 500 store events",
)
def post_ingest(payload: IngestRequest, db: Session = Depends(get_db)):
    """
    Accepts batches of up to 500 events.
    - Idempotent: re-sending the same event_id is a no-op (counted as duplicate).
    - Partial success: malformed events are rejected; valid events in the same
      batch are still persisted.
    - Returns counts: accepted / rejected / duplicate + per-error details.
    """
    structlog.contextvars.bind_contextvars(event_count=len(payload.events))
    result = ingest_events(payload.events, db)
    return result


@app.get(
    "/stores/{store_id}/metrics",
    response_model=StoreMetrics,
    summary="Real-time store metrics for today",
)
def get_store_metrics(store_id: str, db: Session = Depends(get_db)):
    """
    Returns today's metrics (rolling from midnight UTC):
      unique_visitors, conversion_rate, avg_dwell_ms, queue_depth,
      abandonment_rate, zone_dwell breakdown.

    Excludes is_staff=true events.  Handles zero-purchase stores correctly.
    """
    metrics = get_metrics(store_id, db)
    if metrics is None:
        raise HTTPException(status_code=404, detail=f"Store {store_id} not found")
    return metrics


@app.get(
    "/stores/{store_id}/funnel",
    response_model=StoreFunnel,
    summary="Conversion funnel: Entry → Zone Visit → Billing Queue → Purchase",
)
def get_store_funnel(store_id: str, db: Session = Depends(get_db)):
    """
    Session-level funnel.  Re-entries do NOT double-count a visitor —
    each physical person counts once per day regardless of how many
    ENTRY events they produced.
    """
    funnel = get_funnel(store_id, db)
    if funnel is None:
        raise HTTPException(status_code=404, detail=f"Store {store_id} not found")
    return funnel


@app.get(
    "/stores/{store_id}/heatmap",
    response_model=StoreHeatmap,
    summary="Zone visit frequency and dwell, normalised 0–100",
)
def get_store_heatmap(store_id: str, db: Session = Depends(get_db)):
    """
    Returns each zone's normalised visit score (0–100) and avg dwell.
    Sets data_confidence=false when fewer than 20 sessions recorded today.
    """
    heatmap = get_heatmap(store_id, db)
    if heatmap is None:
        raise HTTPException(status_code=404, detail=f"Store {store_id} not found")
    return heatmap


@app.get(
    "/stores/{store_id}/anomalies",
    response_model=StoreAnomalies,
    summary="Active operational anomalies with severity and suggested actions",
)
def get_store_anomalies(store_id: str, db: Session = Depends(get_db)):
    """
    Detects three anomaly types:
      BILLING_QUEUE_SPIKE  — current queue depth exceeds threshold
      CONVERSION_DROP      — today's rate is significantly below 7-day avg
      DEAD_ZONE            — a zone with earlier traffic has gone quiet (30 min)

    Each anomaly includes severity (INFO/WARN/CRITICAL) and suggested_action.
    """
    anomalies = get_anomalies(store_id, db)
    return anomalies


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health and per-store feed freshness",
)
def get_health_status(db: Session = Depends(get_db)):
    """
    Returns overall service status and per-store feed lag.
    Raises STALE_FEED warning if any store's last event is older than 10 minutes.
    """
    return get_health(db)
