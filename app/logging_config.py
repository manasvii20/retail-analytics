"""
Structured logging configuration using structlog.

Every request emits a JSON log line with:
  trace_id, store_id (when available), endpoint, latency_ms,
  event_count (for ingest), status_code.
"""

from __future__ import annotations

import time
import uuid
import structlog
import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# Configure structlog once at import time
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request with trace_id, latency, and status code."""

    async def dispatch(self, request: Request, call_next):
        trace_id = str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id=trace_id)

        # Extract store_id from path if present  (/stores/{store_id}/...)
        path_parts = request.url.path.split("/")
        store_id = None
        if "stores" in path_parts:
            idx = path_parts.index("stores")
            if idx + 1 < len(path_parts):
                store_id = path_parts[idx + 1]
        if store_id:
            structlog.contextvars.bind_contextvars(store_id=store_id)

        start = time.perf_counter()
        response: Response = await call_next(request)
        latency_ms = round((time.perf_counter() - start) * 1000, 2)

        log = structlog.get_logger()
        log.info(
            "request",
            method=request.method,
            endpoint=request.url.path,
            status_code=response.status_code,
            latency_ms=latency_ms,
        )

        response.headers["X-Trace-Id"] = trace_id
        return response
