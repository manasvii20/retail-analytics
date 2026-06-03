"""
Pydantic models matching the required event schema from the problem statement.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"
    PURCHASE = "PURCHASE"

class AnomalySeverity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Event schema
# ---------------------------------------------------------------------------

class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None


class StoreEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EventType
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = Field(..., ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("zone_id")
    @classmethod
    def zone_required_for_zone_events(cls, v, info):
        zone_events = {
            EventType.ZONE_ENTER,
            EventType.ZONE_EXIT,
            EventType.ZONE_DWELL,
            EventType.BILLING_QUEUE_JOIN,
            EventType.BILLING_QUEUE_ABANDON,
        }
        event_type = info.data.get("event_type")
        if event_type in zone_events and v is None:
            raise ValueError(f"zone_id is required for event_type={event_type}")
        return v


# ---------------------------------------------------------------------------
# Ingest request / response
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    events: list[StoreEvent] = Field(..., max_length=500)


class IngestResult(BaseModel):
    accepted: int
    rejected: int
    duplicate: int
    errors: list[dict] = []


# ---------------------------------------------------------------------------
# Metrics response
# ---------------------------------------------------------------------------

class ZoneDwell(BaseModel):
    zone_id: str
    avg_dwell_ms: float
    visit_count: int


class StoreMetrics(BaseModel):
    store_id: str
    as_of: datetime
    unique_visitors: int
    conversion_rate: float          # 0.0–1.0
    avg_dwell_ms: float
    queue_depth: int
    abandonment_rate: float         # 0.0–1.0
    zone_dwell: list[ZoneDwell]


# ---------------------------------------------------------------------------
# Funnel response
# ---------------------------------------------------------------------------

class FunnelStage(BaseModel):
    stage: str
    count: int
    drop_off_pct: float


class StoreFunnel(BaseModel):
    store_id: str
    as_of: datetime
    stages: list[FunnelStage]


# ---------------------------------------------------------------------------
# Heatmap response
# ---------------------------------------------------------------------------

class HeatmapZone(BaseModel):
    zone_id: str
    visit_frequency: float          # normalised 0–100
    avg_dwell_ms: float
    normalised_score: float         # 0–100


class StoreHeatmap(BaseModel):
    store_id: str
    as_of: datetime
    zones: list[HeatmapZone]
    data_confidence: bool           # False when fewer than 20 sessions


# ---------------------------------------------------------------------------
# Anomaly response
# ---------------------------------------------------------------------------

class Anomaly(BaseModel):
    anomaly_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    anomaly_type: str               # BILLING_QUEUE_SPIKE | CONVERSION_DROP | DEAD_ZONE
    severity: AnomalySeverity
    description: str
    suggested_action: str
    detected_at: datetime
    zone_id: Optional[str] = None


class StoreAnomalies(BaseModel):
    store_id: str
    as_of: datetime
    anomalies: list[Anomaly]


# ---------------------------------------------------------------------------
# Health response
# ---------------------------------------------------------------------------

class StoreFeedStatus(BaseModel):
    store_id: str
    last_event_at: Optional[datetime]
    stale: bool                     # True if last event > 10 min ago


class HealthResponse(BaseModel):
    status: str                     # "ok" | "degraded"
    checked_at: datetime
    stores: list[StoreFeedStatus]
    db_connected: bool
