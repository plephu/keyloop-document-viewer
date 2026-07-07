"""Public API: unified document search + search history."""
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..aggregator import aggregate_documents
from ..database import get_db
from ..models import CachedDocument, SearchLog
from ..schemas import (
    AggregatedDocumentsResponse,
    Document,
    SearchLogEntry,
    SourceStatus,
    SourceSystem,
    VinPath,
)

logger = logging.getLogger("api")
router = APIRouter(prefix="/api/v1", tags=["Unified Document Viewer"])


def _validate_vin(vin: str) -> str:
    try:
        return VinPath(vin=vin).vin
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()[0]["msg"])


@router.get("/vehicles/{vin}/documents", response_model=AggregatedDocumentsResponse)
async def get_vehicle_documents(vin: str, request: Request, db: Session = Depends(get_db)):
    """Unified search: aggregate documents for a VIN from both external
    systems. Tolerates partial failure — if one source is down, documents
    from the healthy source are still returned, with per-source status."""
    vin = _validate_vin(vin)
    now = datetime.now(timezone.utc)

    # The mocked external systems are mounted in this same app, so we call
    # them through an ASGI transport (in-process, full HTTP semantics, and
    # it works identically under uvicorn and under TestClient). In
    # production these would be real base URLs + a shared AsyncClient.
    base_url = str(request.base_url).rstrip("/")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=request.app)
    ) as client:
        sources, documents, duration_ms = await aggregate_documents(
            vin, base_url, client
        )

    all_failed = all(s.status != SourceStatus.OK for s in sources)

    # Audit log — always persisted, success or failure.
    db.add(
        SearchLog(
            vin=vin,
            sales_status=sources[0].status.value,
            service_status=sources[1].status.value,
            document_count=len(documents),
            duration_ms=round(duration_ms, 1),
        )
    )

    if all_failed:
        # Graceful degradation: serve stale cache if we have one.
        cached = db.query(CachedDocument).filter(CachedDocument.vin == vin).all()
        db.commit()
        if cached:
            logger.warning("serving_stale_cache", extra={"vin": vin})
            return AggregatedDocumentsResponse(
                vin=vin,
                retrieved_at=now,
                from_cache=True,
                sources=sources,
                documents=[
                    Document(
                        external_id=c.external_id,
                        source_system=SourceSystem(c.source_system),
                        title=c.title,
                        doc_type=c.doc_type,
                        created_date=c.created_date,
                        url=c.url,
                    )
                    for c in cached
                ],
            )
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Both external systems are unavailable",
                "sources": [s.model_dump() for s in sources],
            },
        )

    # Refresh cache with the latest successful aggregation.
    db.query(CachedDocument).filter(CachedDocument.vin == vin).delete()
    for d in documents:
        db.add(
            CachedDocument(
                vin=vin,
                source_system=d.source_system.value,
                external_id=d.external_id,
                title=d.title,
                doc_type=d.doc_type,
                created_date=d.created_date,
                url=d.url,
            )
        )
    db.commit()

    return AggregatedDocumentsResponse(
        vin=vin, retrieved_at=now, sources=sources, documents=documents
    )


@router.get("/search-history", response_model=list[SearchLogEntry])
def get_search_history(limit: int = 20, db: Session = Depends(get_db)):
    """Recent VIN searches (audit trail / observability)."""
    limit = max(1, min(limit, 100))
    return (
        db.query(SearchLog).order_by(SearchLog.searched_at.desc()).limit(limit).all()
    )


@router.get("/health")
def health():
    return {"status": "ok"}
