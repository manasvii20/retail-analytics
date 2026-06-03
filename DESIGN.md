# DESIGN

## Architecture

The system follows an event-driven retail analytics architecture.

1. Video feed is processed using YOLOv8.
2. ByteTrack assigns persistent visitor IDs.
3. Detection events are converted into business events.
4. Events are ingested through FastAPI.
5. Events are stored in SQLite.
6. Analytics APIs compute store intelligence metrics.

Pipeline:

Video → Detection → Tracking → Event Generation → Ingestion API → Database → Analytics APIs

## Components

### Detection Layer

* YOLOv8 person detection
* OpenCV video processing

### Tracking Layer

* ByteTrack multi-object tracking
* Visitor identity persistence

### Event Layer

* ENTRY
* ZONE_ENTER
* BILLING_QUEUE_JOIN
* PURCHASE
* EXIT

### Analytics Layer

* Metrics
* Funnel
* Heatmap
* Anomaly Detection

## Storage

SQLite is used for local persistence and simplicity.

## Scalability

The architecture can be migrated to PostgreSQL and Kafka without major code changes.
