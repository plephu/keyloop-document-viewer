"""API request/response schemas (Pydantic)."""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SourceSystem(str, Enum):
    SALES = "sales_system"
    SERVICE = "service_system"


class SourceStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"


class Document(BaseModel):
    """A single document, normalised from either external system."""
    external_id: str
    source_system: SourceSystem
    title: str
    doc_type: str
    created_date: str
    url: str


class SourceResult(BaseModel):
    """Per-source outcome so the client can show partial-failure state."""
    source: SourceSystem
    status: SourceStatus
    document_count: int = 0
    error_detail: Optional[str] = None


class AggregatedDocumentsResponse(BaseModel):
    vin: str
    retrieved_at: datetime
    from_cache: bool = False
    sources: list[SourceResult]
    documents: list[Document] = Field(
        description="Consolidated list from all sources, newest first."
    )


class VinPath(BaseModel):
    """VIN validation: 17 chars, no I/O/Q (ISO 3779)."""
    vin: str

    @field_validator("vin")
    @classmethod
    def validate_vin(cls, v: str) -> str:
        v = v.strip().upper()
        if len(v) != 17:
            raise ValueError("VIN must be exactly 17 characters")
        if any(c in "IOQ" for c in v):
            raise ValueError("VIN cannot contain the letters I, O, or Q")
        if not v.isalnum():
            raise ValueError("VIN must be alphanumeric")
        return v


class SearchLogEntry(BaseModel):
    id: int
    vin: str
    searched_at: datetime
    sales_status: str
    service_status: str
    document_count: int
    duration_ms: float

    model_config = {"from_attributes": True}
