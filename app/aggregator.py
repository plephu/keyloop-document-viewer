"""Core business logic: fetch from both external systems IN PARALLEL,
normalise their different formats, tolerate partial failure, and merge.

This module is deliberately framework-agnostic (plain async functions,
httpx client injected) so it can be unit-tested without spinning up
FastAPI.
"""
import asyncio
import logging
import time
from typing import Optional

import httpx

from .schemas import Document, SourceResult, SourceSystem, SourceStatus

logger = logging.getLogger("aggregator")

SOURCE_TIMEOUT_SECONDS = 2.0


# ---------------------------------------------------------------------------
# Normalisers: each external system has its own shape -> canonical Document
# ---------------------------------------------------------------------------

def normalise_sales(payload: dict) -> list[Document]:
    return [
        Document(
            external_id=d["docId"],
            source_system=SourceSystem.SALES,
            title=d["docName"],
            doc_type=d["category"].lower(),
            created_date=d["dateCreated"],
            url=d["downloadLink"],
        )
        for d in payload.get("documents", [])
    ]


def normalise_service(payload: dict) -> list[Document]:
    return [
        Document(
            external_id=str(d["id"]),
            source_system=SourceSystem.SERVICE,
            title=d["title"],
            doc_type=d["type"],
            created_date=d["created"][:10],  # ISO datetime -> date
            url=d["file_url"],
        )
        for d in payload.get("files", [])
    ]


# ---------------------------------------------------------------------------
# Fetching with resilience
# ---------------------------------------------------------------------------

async def _fetch_source(
    client: httpx.AsyncClient,
    source: SourceSystem,
    url: str,
    normaliser,
) -> tuple[SourceResult, list[Document]]:
    """Fetch one source. Never raises: failures are captured in SourceResult
    so one dead system can't take down the whole response."""
    try:
        # asyncio.wait_for enforces the deadline regardless of transport
        # (httpx-level timeouts are not honoured by mock/ASGI transports).
        resp = await asyncio.wait_for(client.get(url), timeout=SOURCE_TIMEOUT_SECONDS)
        resp.raise_for_status()
        docs = normaliser(resp.json())
        return (
            SourceResult(source=source, status=SourceStatus.OK, document_count=len(docs)),
            docs,
        )
    except (httpx.TimeoutException, asyncio.TimeoutError):
        logger.warning("source_timeout", extra={"source": source.value, "url": url})
        return (
            SourceResult(
                source=source,
                status=SourceStatus.TIMEOUT,
                error_detail=f"No response within {SOURCE_TIMEOUT_SECONDS}s",
            ),
            [],
        )
    except Exception as exc:  # HTTP errors, bad JSON, connection refused...
        logger.warning(
            "source_error", extra={"source": source.value, "error": str(exc)}
        )
        return (
            SourceResult(source=source, status=SourceStatus.ERROR, error_detail=str(exc)),
            [],
        )


async def aggregate_documents(
    vin: str,
    base_url: str,
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[list[SourceResult], list[Document], float]:
    """Fetch from both systems concurrently and merge.

    Returns (per-source results, consolidated documents newest-first,
    total duration in ms).
    """
    sales_url = f"{base_url}/mock/sales-system/documents/{vin}"
    service_url = f"{base_url}/mock/service-system/records/{vin}/files"

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()

    start = time.perf_counter()
    try:
        # THE core requirement: parallel requests to both external APIs.
        (sales_result, sales_docs), (service_result, service_docs) = await asyncio.gather(
            _fetch_source(client, SourceSystem.SALES, sales_url, normalise_sales),
            _fetch_source(client, SourceSystem.SERVICE, service_url, normalise_service),
        )
    finally:
        if owns_client:
            await client.aclose()

    duration_ms = (time.perf_counter() - start) * 1000

    documents = sorted(
        sales_docs + service_docs,
        key=lambda d: d.created_date,
        reverse=True,
    )
    return [sales_result, service_result], documents, duration_ms
