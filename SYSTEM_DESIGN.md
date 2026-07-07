# System Design Document — Unified Document Viewer

**Keyloop Technical Assessment — Scenario D (Operate domain)**
**Implementation choice: Backend** (REST API + persistent database; client mocked via OpenAPI spec + cURL examples)

---

## 1. Problem Statement

Dealership staff currently look up vehicle documents in two separate systems
(a Sales System and a Service System). This service provides a **single VIN
search** that aggregates documents from both systems into one consolidated,
source-labelled view — and stays useful even when one of those systems is
slow or down.

## 2. Architecture Diagram

```
                        ┌──────────────────────────────────────────────┐
                        │              Document Viewer API             │
                        │                  (FastAPI)                   │
┌──────────┐  GET /api/v1/vehicles/{vin}/documents                     │
│  Client  │──────────▶ │  ┌────────────┐      ┌────────────────────┐  │
│ (mocked: │            │  │ VIN        │      │ Aggregator          │  │
│ OpenAPI  │◀────────── │  │ Validator  │─────▶│ asyncio.gather()    │  │
│ + cURL)  │  JSON      │  └────────────┘      │  ├─ fetch sales ────┼──┼──▶ ┌─────────────────┐
└──────────┘            │                      │  └─ fetch service ──┼──┼──▶ │ Mock Sales API   │
                        │  ┌─────────────────┐ │  (2s timeout each,  │  │    │ (own format)     │
                        │  │ SQLite/Postgres │◀┤   per-source status)│  │    ├─────────────────┤
                        │  │ ├ search_logs   │ └────────────────────┘  │    │ Mock Service API │
                        │  │ └ cached_docs   │   normalise → merge     │    │ (different fmt)  │
                        │  └─────────────────┘   → sort newest-first   │    └─────────────────┘
                        └──────────────────────────────────────────────┘
```

## 3. Components

| Component | Role |
|---|---|
| **API layer** (`app/routers/documents.py`) | Public REST endpoints: unified search, search history, health. VIN validation (ISO 3779: 17 chars, no I/O/Q). |
| **Aggregator** (`app/aggregator.py`) | The core business logic. Framework-agnostic: fires both external calls **in parallel** (`asyncio.gather`), enforces a 2s per-source deadline, converts every failure into a per-source status instead of an exception, normalises two different payload shapes into one canonical `Document` model, merges and sorts newest-first. |
| **Mock external systems** (`app/routers/mocks/`) | Two mocked APIs with **deliberately different response formats** (as real legacy systems would have). They also expose failure modes via special VINs (…E → 500, …F → 503, …T → 5s hang) so resilience can be demonstrated live. |
| **Persistence** (`app/models.py`) | `search_logs` (audit trail of every lookup: statuses, doc count, latency) and `cached_documents` (last successful aggregation per VIN — served as stale fallback when *both* sources are down). |
| **Observability middleware** (`app/main.py`) | Structured JSON logs with request IDs, per-request latency; `X-Request-ID` response header for traceability. |

## 4. Data Flow

1. Client calls `GET /api/v1/vehicles/{vin}/documents`.
2. VIN is validated (422 on malformed input — fail fast, no external calls).
3. Aggregator fires **two parallel HTTP requests** to the Sales and Service systems, each with an independent 2-second deadline.
4. Each source resolves to `ok`, `error`, or `timeout`. **A failing source never breaks the response**: documents from the healthy source are returned with per-source status flags.
5. Both payload shapes are normalised into one canonical `Document` (with `source_system` label — the "clearly indicating the source" requirement), merged, and sorted newest-first.
6. A `SearchLog` row is persisted (always), and `cached_documents` is refreshed (on success).
7. If **both** sources fail: serve the stale cache (`from_cache: true`) if one exists, otherwise return `502` with per-source diagnostics.

## 5. Technology Choices & Justification

| Choice | Why |
|---|---|
| **Python + FastAPI** | Native `async` makes the parallel-fetch requirement idiomatic; auto-generated OpenAPI docs at `/docs` double as the required API contract for the mocked client layer. |
| **httpx (async client)** | Async HTTP with clean testing story (`MockTransport` for unit tests, `ASGITransport` for in-process calls). |
| **SQLAlchemy + SQLite** | Zero-setup persistence for review; the ORM layer means switching to PostgreSQL in production is a one-line `DATABASE_URL` change. |
| **pytest + pytest-asyncio** | Unit tests for the aggregator (no server needed) plus full-stack integration tests through `TestClient`. |
| **asyncio.wait_for for deadlines** | Enforces the per-source timeout at the application layer, independent of transport behaviour (httpx-level timeouts are not honoured by mock/ASGI transports — discovered via a failing test, see AI Collaboration Narrative). |

## 6. Key Design Decisions & Assumptions

Documented per the brief's "Note on Ambiguity":

1. **What to persist.** The external systems remain the source of truth for documents, so the DB stores (a) a **search audit log** — valuable in a dealership compliance context — and (b) a **document cache** used only for graceful degradation when both sources are down.
2. **Partial failure returns 200, not an error.** A dealership user with one system down still needs the other system's documents. Per-source `status` fields let the UI show "Service system unavailable" banners.
3. **Per-source timeout = 2s.** Aggregate latency is bounded by the slowest source, not the sum — verified by a test asserting total duration.
4. **Mock systems live in the same process** but are called over full HTTP semantics (ASGI transport), so swapping to real external URLs changes configuration, not code.
5. **VIN validation** follows ISO 3779 basics (17 alphanumeric chars, no I/O/Q), normalised to uppercase.

## 7. Observability Strategy

- **Logging:** structured JSON logs (machine-parseable) with request ID, method, path, status, and latency for every request; warning-level events for source errors/timeouts and stale-cache serves.
- **Tracing:** `X-Request-ID` header returned on every response; the same ID appears in logs, enabling request correlation. In production this would be replaced by OpenTelemetry trace propagation to the external systems.
- **Metrics:** per-search latency and per-source status are persisted in `search_logs`, queryable via `/api/v1/search-history` — effectively a built-in mini-dashboard of source health over time. In production: Prometheus counters (requests, source failures) + histograms (latency) scraped from a `/metrics` endpoint.

## 8. Build for the Future

- **Scalability:** stateless API → horizontal scaling behind a load balancer; SQLite → PostgreSQL swap is config-only; the cache table could move to Redis with TTLs.
- **Performance:** parallel fan-out bounds latency at max(source latencies); connection pooling via a shared `AsyncClient` in production.
- **Reliability:** per-source timeouts, partial-failure tolerance, stale-cache fallback. Next step: circuit breakers per source (e.g. after N consecutive failures, skip a source for a cooldown window).
- **Maintainability:** aggregator is pure/framework-agnostic and fully unit-tested; adding a third source = one normaliser function + one line in `asyncio.gather`.

## 9. How GenAI Assisted in the Design Phase

*(See README.md → AI Collaboration Narrative for the full story.)*

- Used an AI agent (Claude) to **compare the four scenarios** against the evaluation rubric and pick the one with the strongest design story (parallel aggregation, resilience) relative to implementation size.
- Asked the AI to **propose what "persistent database" should mean** for a read-aggregation scenario; reviewed its options (cache vs. audit log vs. full document mirror) and chose cache + audit log, rejecting a full mirror as a data-ownership anti-pattern.
- Had the AI draft the failure-mode matrix (one down / both down / slow / malformed payload) which became both the error-handling design and the test plan.
