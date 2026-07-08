"""Integration tests: full FastAPI app + SQLite DB + the mocked external
systems, exercised through the real HTTP stack (TestClient).
"""
import os
import tempfile

import pytest

# Use a throwaway DB file per test session, set BEFORE importing the app.
_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers import documents as documents_router  # noqa: E402
from app.schemas import SourceResult, SourceStatus, SourceSystem  # noqa: E402

KNOWN_VIN = "1HGBH41JXMN109186"     # documents in both systems
EMPTY_VIN = "WBA3A5C58DF123456"     # valid VIN, no documents anywhere
SALES_DOWN_VIN = "WBA3A5C58DF12345E"  # ends in E -> sales system 500
BOTH_DOWN_VIN = "ZFA2500000MDLN5F"   # ends in F -> service 503; sales empty? no:
# For BOTH down we need sales to fail too. Sales fails on VINs ending in E.
# So simulate total failure with the timeout VIN instead (both sleep 5s).
TIMEOUT_VIN = "JH4KA7561PC0000TT"     # ends in T -> both sources sleep 5s
INVALID_VIN = "TOO-SHORT"


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_invalid_vin_rejected_with_422(client):
    r = client.get(f"/api/v1/vehicles/{INVALID_VIN}/documents")
    assert r.status_code == 422
    assert "17 characters" in r.json()["detail"]


def test_vin_with_forbidden_letters_rejected(client):
    r = client.get("/api/v1/vehicles/QQZ3A5C58DF123456/documents")
    assert r.status_code == 422


def test_known_vin_returns_consolidated_documents(client):
    r = client.get(f"/api/v1/vehicles/{KNOWN_VIN}/documents")
    assert r.status_code == 200
    body = r.json()

    assert body["vin"] == KNOWN_VIN
    assert body["from_cache"] is False
    assert {s["source"] for s in body["sources"]} == {"sales_system", "service_system"}
    assert all(s["status"] == "ok" for s in body["sources"])

    # 3 sales docs + 2 service docs, each labelled with its source
    assert len(body["documents"]) == 5
    sources_seen = {d["source_system"] for d in body["documents"]}
    assert sources_seen == {"sales_system", "service_system"}

    # Newest first
    dates = [d["created_date"] for d in body["documents"]]
    assert dates == sorted(dates, reverse=True)


def test_lowercase_vin_is_normalised(client):
    r = client.get(f"/api/v1/vehicles/{KNOWN_VIN.lower()}/documents")
    assert r.status_code == 200
    assert r.json()["vin"] == KNOWN_VIN


def test_unknown_vin_returns_empty_list_not_error(client):
    r = client.get(f"/api/v1/vehicles/{EMPTY_VIN}/documents")
    assert r.status_code == 200
    assert r.json()["documents"] == []


def test_one_source_down_returns_partial_results(client):
    r = client.get(f"/api/v1/vehicles/{SALES_DOWN_VIN}/documents")
    assert r.status_code == 200
    body = r.json()
    statuses = {s["source"]: s["status"] for s in body["sources"]}
    assert statuses["sales_system"] == "error"
    assert statuses["service_system"] == "ok"


def test_both_sources_timing_out_returns_502_when_no_cache(client):
    r = client.get(f"/api/v1/vehicles/{TIMEOUT_VIN}/documents")
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["message"] == "Both external systems are unavailable"
    assert all(s["status"] == "timeout" for s in detail["sources"])


def _force_all_sources_down(monkeypatch):
    """Replace the aggregator so both sources report failure — the mocks
    can't fail for a VIN that already has cached documents (failure is
    keyed on the VIN suffix), so we patch at the router boundary."""

    async def all_failed(vin, base_url, client=None):
        return (
            [
                SourceResult(source=SourceSystem.SALES,
                             status=SourceStatus.ERROR, error_detail="down"),
                SourceResult(source=SourceSystem.SERVICE,
                             status=SourceStatus.TIMEOUT, error_detail="down"),
            ],
            [],
            12.3,
        )

    monkeypatch.setattr(documents_router, "aggregate_documents", all_failed)


def test_both_sources_down_serves_stale_cache(client, monkeypatch):
    # 1. Successful search populates the cache.
    r = client.get(f"/api/v1/vehicles/{KNOWN_VIN}/documents")
    assert r.status_code == 200
    live_docs = r.json()["documents"]

    # 2. Both sources go down -> stale cache is served instead of a 502.
    _force_all_sources_down(monkeypatch)
    r = client.get(f"/api/v1/vehicles/{KNOWN_VIN}/documents")
    assert r.status_code == 200
    body = r.json()
    assert body["from_cache"] is True
    assert {s["status"] for s in body["sources"]} == {"error", "timeout"}
    assert len(body["documents"]) == len(live_docs) == 5
    assert (
        {d["external_id"] for d in body["documents"]}
        == {d["external_id"] for d in live_docs}
    )


def test_repeated_search_refreshes_cache_without_duplicates(client, monkeypatch):
    # Two successful searches for the same VIN must replace, not append.
    client.get(f"/api/v1/vehicles/{KNOWN_VIN}/documents")
    r = client.get(f"/api/v1/vehicles/{KNOWN_VIN}/documents")
    assert r.status_code == 200
    assert r.json()["from_cache"] is False
    assert len(r.json()["documents"]) == 5

    # The cache (read via the stale-cache path) must also hold exactly 5.
    _force_all_sources_down(monkeypatch)
    r = client.get(f"/api/v1/vehicles/{KNOWN_VIN}/documents")
    assert r.json()["from_cache"] is True
    assert len(r.json()["documents"]) == 5


def test_failed_search_is_logged_in_history(client):
    r = client.get(f"/api/v1/vehicles/{TIMEOUT_VIN}/documents")
    assert r.status_code == 502

    entries = client.get("/api/v1/search-history?limit=100").json()
    entry = next(e for e in entries if e["vin"] == TIMEOUT_VIN)
    assert entry["sales_status"] == "timeout"
    assert entry["service_status"] == "timeout"
    assert entry["document_count"] == 0


def test_search_history_limit_is_clamped(client):
    # Make sure at least two history entries exist.
    client.get(f"/api/v1/vehicles/{KNOWN_VIN}/documents")
    client.get(f"/api/v1/vehicles/{EMPTY_VIN}/documents")

    # limit below 1 is clamped up to 1
    assert len(client.get("/api/v1/search-history?limit=0").json()) == 1
    assert len(client.get("/api/v1/search-history?limit=-5").json()) == 1
    # limit above 100 is clamped down to 100
    r = client.get("/api/v1/search-history?limit=1000")
    assert r.status_code == 200
    assert len(r.json()) <= 100
    # non-numeric limit is rejected by validation
    assert client.get("/api/v1/search-history?limit=abc").status_code == 422


def test_search_history_is_persisted(client):
    client.get(f"/api/v1/vehicles/{KNOWN_VIN}/documents")
    r = client.get("/api/v1/search-history?limit=10")
    assert r.status_code == 200
    entries = r.json()
    assert len(entries) >= 1
    entry = next(e for e in entries if e["vin"] == KNOWN_VIN)
    assert entry["document_count"] == 5
    assert entry["duration_ms"] >= 0
    assert entry["sales_status"] == "ok"
