"""Unified Document Viewer — application entry point.

Keyloop Technical Assessment, Scenario D (backend implementation).
"""
import json
import logging
import time
import uuid

from fastapi import FastAPI, Request

from .database import Base, engine
from .routers import documents
from .routers.mocks import sales_api, service_api


# --- Structured JSON logging (observability) -------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status", "duration_ms",
                    "source", "vin", "url", "error"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("http")

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Unified Document Viewer API",
    description=(
        "Aggregates vehicle documents from two mocked dealership systems "
        "(Sales System API and Service System API) into a single view. "
        "Keyloop Technical Assessment - Scenario D."
    ),
    version="1.0.0",
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Every request gets a request_id and a structured access-log entry —
    the basis for tracing and latency metrics."""
    request_id = str(uuid.uuid4())[:8]
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    logger.info(
        "request_completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    response.headers["X-Request-ID"] = request_id
    return response


# Public API
app.include_router(documents.router)
# Mocked external systems (would be separate deployments in production)
app.include_router(sales_api.router)
app.include_router(service_api.router)
