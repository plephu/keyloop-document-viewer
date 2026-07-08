"""Unit tests for the core business logic (app/aggregator.py).

These test the aggregator in isolation using httpx.MockTransport —
no server, no database. Covers:
- normalisation of two different source formats
- parallel fetch and merge ordering
- partial failure (one source down)
- total failure
- per-source timeout (and proof of parallelism via total duration)
"""
import asyncio

import httpx
import pytest

from app.aggregator import (
    SOURCE_TIMEOUT_SECONDS,
    aggregate_documents,
    normalise_sales,
    normalise_service,
)
from app.schemas import SourceStatus, SourceSystem

BASE = "http://testserver"
VIN = "1HGBH41JXMN109186"

SALES_PAYLOAD = {
    "vehicleVin": VIN,
    "documents": [
        {
            "docId": "S-1",
            "docName": "Purchase Agreement",
            "category": "CONTRACT",
            "dateCreated": "2024-03-15",
            "downloadLink": "https://x/s1.pdf",
        }
    ],
}

SERVICE_PAYLOAD = {
    "vin": VIN,
    "files": [
        {
            "id": 9001,
            "title": "Service Invoice",
            "type": "invoice",
            "created": "2025-06-02T09:30:00Z",
            "file_url": "https://x/9001.pdf",
        }
    ],
}


def make_client(sales_response, service_response) -> httpx.AsyncClient:
    """Build an httpx client whose transport returns canned responses."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if "/mock/sales-system/" in str(request.url):
            return sales_response
        return service_response

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- Normalisation ----------------------------------------------------------

def test_normalise_sales_maps_fields_to_canonical_model():
    docs = normalise_sales(SALES_PAYLOAD)
    assert len(docs) == 1
    d = docs[0]
    assert d.external_id == "S-1"
    assert d.source_system == SourceSystem.SALES
    assert d.title == "Purchase Agreement"
    assert d.doc_type == "contract"  # lowercased
    assert d.created_date == "2024-03-15"


def test_normalise_service_truncates_datetime_to_date():
    docs = normalise_service(SERVICE_PAYLOAD)
    assert len(docs) == 1
    d = docs[0]
    assert d.external_id == "9001"
    assert d.source_system == SourceSystem.SERVICE
    assert d.created_date == "2025-06-02"


def test_normalise_handles_empty_payloads():
    assert normalise_sales({"documents": []}) == []
    assert normalise_service({}) == []


# --- Aggregation ------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_merges_both_sources_newest_first():
    client = make_client(
        httpx.Response(200, json=SALES_PAYLOAD),
        httpx.Response(200, json=SERVICE_PAYLOAD),
    )
    sources, docs, _ = await aggregate_documents(VIN, BASE, client)

    assert all(s.status == SourceStatus.OK for s in sources)
    assert len(docs) == 2
    # Service doc (2025) must come before sales doc (2024)
    assert docs[0].source_system == SourceSystem.SERVICE
    assert docs[1].source_system == SourceSystem.SALES


@pytest.mark.asyncio
async def test_partial_failure_still_returns_healthy_source_documents():
    client = make_client(
        httpx.Response(500, json={"detail": "boom"}),
        httpx.Response(200, json=SERVICE_PAYLOAD),
    )
    sources, docs, _ = await aggregate_documents(VIN, BASE, client)

    sales, service = sources
    assert sales.status == SourceStatus.ERROR
    assert service.status == SourceStatus.OK
    # The healthy source's documents are still returned.
    assert len(docs) == 1
    assert docs[0].source_system == SourceSystem.SERVICE


@pytest.mark.asyncio
async def test_total_failure_returns_no_documents_but_does_not_raise():
    client = make_client(
        httpx.Response(500, json={}),
        httpx.Response(503, json={}),
    )
    sources, docs, _ = await aggregate_documents(VIN, BASE, client)
    assert all(s.status == SourceStatus.ERROR for s in sources)
    assert docs == []


@pytest.mark.asyncio
async def test_slow_source_times_out_without_blocking_the_other():
    async def slow_handler(request: httpx.Request) -> httpx.Response:
        if "/mock/sales-system/" in str(request.url):
            await asyncio.sleep(SOURCE_TIMEOUT_SECONDS + 5)
            return httpx.Response(200, json=SALES_PAYLOAD)
        return httpx.Response(200, json=SERVICE_PAYLOAD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(slow_handler))
    sources, docs, duration_ms = await aggregate_documents(VIN, BASE, client)

    sales, service = sources
    assert sales.status == SourceStatus.TIMEOUT
    assert service.status == SourceStatus.OK
    assert len(docs) == 1
    # Proof of parallelism: total time ~= the timeout of the slowest source,
    # NOT the sum of both requests.
    assert duration_ms < (SOURCE_TIMEOUT_SECONDS + 2) * 1000


@pytest.mark.asyncio
async def test_malformed_json_from_source_is_treated_as_error():
    client = make_client(
        httpx.Response(200, content=b"not-json",
                       headers={"content-type": "application/json"}),
        httpx.Response(200, json=SERVICE_PAYLOAD),
    )
    sources, docs, _ = await aggregate_documents(VIN, BASE, client)
    assert sources[0].status == SourceStatus.ERROR
    assert len(docs) == 1


# --- Malformed / edge-case payloads ------------------------------------------

def test_normalise_sales_raises_on_document_missing_required_field():
    bad = {
        "documents": [
            {  # no "docId"
                "docName": "Orphan",
                "category": "CONTRACT",
                "dateCreated": "2024-01-01",
                "downloadLink": "https://x/o.pdf",
            }
        ]
    }
    with pytest.raises(KeyError):
        normalise_sales(bad)


@pytest.mark.asyncio
async def test_source_with_one_bad_document_is_all_or_nothing():
    """Pin current behaviour: a single malformed document poisons its whole
    source (marked ERROR, all its docs dropped), but the other source's
    documents still come through."""
    mixed = {
        "vehicleVin": VIN,
        "documents": [
            SALES_PAYLOAD["documents"][0],  # valid
            {"docName": "missing docId"},   # malformed
        ],
    }
    client = make_client(
        httpx.Response(200, json=mixed),
        httpx.Response(200, json=SERVICE_PAYLOAD),
    )
    sources, docs, _ = await aggregate_documents(VIN, BASE, client)
    assert sources[0].status == SourceStatus.ERROR
    assert sources[1].status == SourceStatus.OK
    assert [d.source_system for d in docs] == [SourceSystem.SERVICE]


@pytest.mark.asyncio
async def test_null_json_body_is_treated_as_error():
    client = make_client(
        httpx.Response(200, content=b"null",
                       headers={"content-type": "application/json"}),
        httpx.Response(200, json=SERVICE_PAYLOAD),
    )
    sources, docs, _ = await aggregate_documents(VIN, BASE, client)
    assert sources[0].status == SourceStatus.ERROR
    assert len(docs) == 1


def test_normalise_service_keeps_short_created_string_as_is():
    payload = {
        "files": [
            {
                "id": 1,
                "title": "t",
                "type": "invoice",
                "created": "2025-06",  # shorter than a full date
                "file_url": "https://x/1.pdf",
            }
        ]
    }
    assert normalise_service(payload)[0].created_date == "2025-06"


@pytest.mark.asyncio
async def test_equal_created_dates_keeps_all_documents_in_stable_order():
    same_day = "2025-06-02"
    sales = {
        "vehicleVin": VIN,
        "documents": [
            {
                "docId": "S-1",
                "docName": "Contract",
                "category": "CONTRACT",
                "dateCreated": same_day,
                "downloadLink": "https://x/s1.pdf",
            }
        ],
    }
    service = {
        "vin": VIN,
        "files": [
            {
                "id": 9001,
                "title": "Invoice",
                "type": "invoice",
                "created": f"{same_day}T09:30:00Z",
                "file_url": "https://x/9001.pdf",
            }
        ],
    }
    client = make_client(
        httpx.Response(200, json=sales),
        httpx.Response(200, json=service),
    )
    _, docs, _ = await aggregate_documents(VIN, BASE, client)
    assert len(docs) == 2
    # sorted() is stable: on a date tie, concatenation order (sales first) holds.
    assert [d.source_system for d in docs] == [SourceSystem.SALES, SourceSystem.SERVICE]
