# Retail Analytics & Store Intelligence Platform

## Overview

This project is an AI-powered retail analytics platform.

The system processes retail store video feeds, detects and tracks customers, generates behavioral events, and provides actionable business insights through analytics APIs.

## Features

* YOLOv8-based customer detection
* ByteTrack multi-object tracking
* Event generation pipeline
* FastAPI backend
* Real-time metrics API
* Conversion funnel analytics
* Heatmap generation
* Anomaly detection
* SQLite storage
* Automated test suite

## Architecture

Video Feed
→ YOLOv8 Detection
→ ByteTrack Tracking
→ Event Generation
→ Event Ingestion API
→ SQLite Database
→ Analytics APIs

## APIs

### POST /events/ingest

Ingest customer behavior events.

### GET /stores/{store_id}/metrics

Store KPIs including visitors and conversion rate.

### GET /stores/{store_id}/funnel

Entry → Zone Visit → Billing Queue → Purchase funnel.

### GET /stores/{store_id}/heatmap

Zone popularity and dwell analytics.

### GET /stores/{store_id}/anomalies

Operational anomaly detection.

## Testing

All tests pass successfully.

31 / 31 Tests Passed

## Running

Install dependencies:

pip install -r requirements.txt

Start API:

uvicorn app.main:app --reload

Run detection:

python pipeline/detect.py

Emit events:

python pipeline/emit.py

Run tests:

pytest -v

## Tech Stack

* Python
* FastAPI
* SQLite
* OpenCV
* YOLOv8
* ByteTrack
* PyTest
* Docker

## Future Improvements

* Multi-camera support
* POS integration
* Advanced customer journey analytics
* Real-time dashboards
