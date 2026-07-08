# Unified Document Viewer — Backend

**Keyloop Technical Assessment — Scenario D** (Operate domain), backend implementation.

A single VIN search that aggregates vehicle documents from two mocked dealership
systems (**Sales System API** and **Service System API**) — fetched **in parallel**,
normalised into one consolidated, source-labelled list, and resilient to either
source being slow or down.

📄 Architecture, data flow, and design decisions: [SYSTEM_DESIGN.md](./SYSTEM_DESIGN.md)

---

## Quick Start

Requires Python 3.11+.

```bash
python -m venv venv && source venv/bin/activate   
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Windows Powershell:
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

- Interactive API docs (OpenAPI): [http://localhost:8000/docs](http://localhost:8000/docs)
- Health check: [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)



## Run the Tests

```bash
python -m pytest tests/ -v
```

17 tests: unit tests for the core aggregation logic (normalisation, parallel
fetch, partial failure, timeout, malformed payloads) + full-stack integration
tests (VIN validation, consolidation, source labelling, audit persistence).

## Try It (cURL examples — the mocked client layer)

```bash
# Happy path: documents from BOTH systems, merged newest-first,
# each labelled with its source_system
curl -s http://localhost:8000/api/v1/vehicles/1HGBH41JXMN109186/documents | python -m json.tool

# Second seeded vehicle
curl -s http://localhost:8000/api/v1/vehicles/5YJSA1E26MF123456/documents | python -m json.tool

# Partial failure: VIN ending in "E" makes the Sales system return 500.
# Response is still 200 — Service documents are returned, with per-source status.
curl -s http://localhost:8000/api/v1/vehicles/WBA3A5C58DF12345E/documents | python -m json.tool

# Both sources down (VIN ending in "T" makes both hang past the 2s timeout):
# -> 502 with per-source diagnostics (or stale cache if one exists)
curl -s http://localhost:8000/api/v1/vehicles/JH4KA7561PC0000TT/documents | python -m json.tool

# Invalid VIN -> 422, no external calls made
curl -s http://localhost:8000/api/v1/vehicles/TOOSHORT/documents

# Search audit log (persisted in the database)
curl -s "http://localhost:8000/api/v1/search-history?limit=10" | python -m json.tool
```



### Demo VIN cheat-sheet


| VIN                         | Behaviour                                            |
| --------------------------- | ---------------------------------------------------- |
| `1HGBH41JXMN109186`         | 3 sales + 2 service documents                        |
| `5YJSA1E26MF123456`         | 1 sales + 2 service documents                        |
| any valid VIN ending in `E` | Sales system returns 500 → partial results           |
| any valid VIN ending in `F` | Service system returns 503 → partial results         |
| any valid VIN ending in `T` | Both sources hang → timeout path (502 / stale cache) |




## Project Layout

```
app/
├── main.py                 # FastAPI app + structured-JSON logging middleware
├── aggregator.py           # CORE LOGIC: parallel fetch, timeouts, normalise, merge
├── database.py             # SQLAlchemy engine/session (SQLite dev, Postgres-ready)
├── models.py               # search_logs (audit) + cached_documents (fallback)
├── schemas.py              # Pydantic models incl. ISO-3779 VIN validation
└── routers/
    ├── documents.py        # Public API: unified search, history, health
    └── mocks/              # Two mocked external systems, different formats
tests/
├── test_aggregator.py      # Unit tests for business logic (no server)
└── test_api.py             # Integration tests through the HTTP stack
```

---



## AI Collaboration Narrative



### Strategy for directing the AI

I treated the AI (Claude) as a senior pair-programmer that I direct, verify, and
overrule — not as an autocomplete. My process had four phases:

1. **Scenario selection.** Before writing any code, I had the AI analyse all
  four scenarios against the published evaluation rubric and argue a
   points-per-effort recommendation. It surfaced that Scenario D has the
   clearest architectural story (parallel fan-out, partial failure, per-source
   timeouts) at a manageable implementation size. I made the final call.
2. **Design before code.** I asked for design options on the ambiguous parts —
  most importantly *what "persistent database" means in a read-aggregation
   scenario*. The AI proposed three options (full document mirror, cache,
   audit log). I rejected the full mirror (data-ownership anti-pattern:
   the external systems are the source of truth) and chose audit log + stale
   cache, which also gave a graceful-degradation story.
3. **Constrained implementation.** I directed the AI to keep the aggregator
  **framework-agnostic** (plain async functions, injected HTTP client) so the
   core business logic is unit-testable without FastAPI, and required that the
   two mock systems use *different* payload shapes to force real normalisation.
4. **Tests as the verification contract.** I required a failure-mode matrix
  (one source down / both down / slow source / malformed JSON / bad VIN)
   up front, and turned it into the test plan before accepting implementation.



### Verifying and refining the AI's output

The test suite caught two real defects in AI-generated code, which is exactly
why I insisted on it:

- **Timeouts silently not enforced.** The first implementation relied on
`httpx`'s request timeout. The "slow source" unit test failed — because
mock/ASGI transports don't honour httpx timeouts, and the same would have
bitten any in-process test setup. Fix: enforce the deadline with
`asyncio.wait_for(...)` at the application layer, which is transport-independent.
The test also asserts *total* duration ≈ one timeout, proving requests
genuinely run in parallel.
- **Self-calls failing under the test client.** Integration tests failed
because the app tried to reach its own mock routes over a real socket that
`TestClient` never opens. Fix: route the "external" calls through
`httpx.ASGITransport`, keeping full HTTP semantics while working identically
under uvicorn and under tests. (In production this becomes real base URLs —
a config change, not a code change.)
- Smaller catches from review: test VINs that were 16 characters long (my own
validator correctly rejected them — a nice mutual check between validation
code and tests), and timezone-aware timestamps for the audit log.



### Ensuring final quality

- All 17 tests green; ran the real server and exercised every documented cURL
example, including both failure modes, and verified the structured logs and
`X-Request-ID` correlation.
- Read every line of the final code and can defend each decision — the
design-decisions section of SYSTEM_DESIGN.md is that defence in writing.

