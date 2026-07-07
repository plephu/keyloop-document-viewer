"""Persistence models.

Design assumption (documented in SYSTEM_DESIGN.md): the source of truth
for documents lives in the two external systems. Our backend persists:

1. SearchLog      - audit trail of every VIN lookup (observability +
                    compliance in a dealership context).
2. CachedDocument - cache of the last successful aggregation, so users
                    still get (stale) data if both sources are down.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SearchLog(Base):
    __tablename__ = "search_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vin: Mapped[str] = mapped_column(String(17), index=True)
    searched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sales_status: Mapped[str] = mapped_column(String(16))    # ok | error | timeout
    service_status: Mapped[str] = mapped_column(String(16))  # ok | error | timeout
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)


class CachedDocument(Base):
    __tablename__ = "cached_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vin: Mapped[str] = mapped_column(String(17), index=True)
    source_system: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    doc_type: Mapped[str] = mapped_column(String(64))
    created_date: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(String(512))
    cached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
