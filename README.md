# XENOM Intelligence

XENOM is a full-stack research and company-intelligence workspace. I built it to keep a visible trail from collected material to the claims, events, and recommendations shown in the dashboard. The goal is useful analysis that people can inspect, not an answer that asks to be trusted on its own.

The app brings together a FastAPI service, a React and TypeScript dashboard, and PostgreSQL with pgvector. It can run with its deterministic rules and lexical search alone; embeddings and language-model features are optional integrations, not simulated defaults.

## What it does

- Collects material from configured feeds and web sources, then normalizes, deduplicates, scores, and quality-gates it before storage.
- Keeps articles, entities, relationships, claims, citations, and events connected so a reviewer can follow the evidence behind an insight.
- Builds company and competitor views, timelines, researcher profiles, opportunities, alerts, and briefings from stored evidence.
- Provides source administration, versioned profiles, review workflows, and evaluation tools for checking the quality of the pipeline.
- Offers lexical search out of the box. Semantic and hybrid retrieval can be enabled with an embedding provider.
- Includes an optional research copilot and AI-assisted claim analysis. Provider calls are configurable and recorded against a daily call budget.

Derived information is intended to remain reviewable. A potential opportunity or an unverified capability is not presented as a confirmed fact.

## How it fits together

```text
Configured sources
      |
      v
Fetch -> normalize -> deduplicate -> relevance / quality gate
      |
      v
PostgreSQL: articles, entities, evidence, claims, events, and profiles
      |
      +--> Search and timelines
      +--> Insights, opportunities, alerts, and briefings
      +--> Dashboard and optional AI-assisted analysis
```

The API starts a background ingestion scheduler by default. It runs a cycle on startup and repeats every ten minutes; set `SCHEDULER_ENABLED=false` in `.env` to pause it during local work. Source availability and rate limits can affect what a run collects.

## Stack

- **Backend:** Python 3.12+, FastAPI, SQLAlchemy async, Alembic
- **Database:** PostgreSQL 16 with pgvector
- **Frontend:** React, TypeScript, Vite, Tailwind CSS
- **Optional integrations:** Brave Search, local or OpenAI-compatible embeddings, and Anthropic, Groq, or OpenAI-compatible chat APIs

## Run locally

You will need Python 3.12+, Node.js 22+, and Docker Desktop. Start in PowerShell from the repository root.

Create local configuration and start PostgreSQL:

```powershell
Copy-Item .env.example .env
docker compose up -d postgres
```

Review `.env` before continuing. Change the database password and JWT secret, and set `DEV_USER_PASSWORD` to a local-only value. The example file is for development; do not reuse its values in a shared or deployed environment.

PostgreSQL uses host port `5433` by default. If that port is already occupied, change `POSTGRES_PORT` and the port in `DATABASE_URL` in `.env` to the same free port.

Install the backend and initialize the database:

```powershell
Set-Location backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
python -m app.db.seed
uvicorn app.main:app --reload --port 8000
```

In a second terminal, install and run the dashboard:

```powershell
Set-Location frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5176`. The Vite development server proxies `/api` to `http://localhost:8000` by default. The seeded local administrator signs in as `dev@xenom.local`; its password is the `DEV_USER_PASSWORD` value from `.env`.

The API is at `http://localhost:8000`; interactive API documentation is at `http://localhost:8000/docs`. `GET /health` checks database connectivity.

## Docker backend

The Compose file publishes PostgreSQL on the `POSTGRES_PORT` value from `.env` (default `5433`) and the API on port `8000`. It does not run migrations automatically. From the repository root:

```powershell
Copy-Item .env.example .env
docker compose up -d postgres
docker compose run --rm backend alembic upgrade head
docker compose run --rm backend python -m app.db.seed
docker compose up --build -d backend
```

Use `docker compose logs -f backend` to follow the API and scheduler logs. The frontend can still run locally with `npm run dev`; to point it at a different API host, set `VITE_PROXY_TARGET` before starting Vite.

## Search and AI configuration

`SEARCH_PROVIDER=fixture` enables the clearly synthetic fixture search used for local demonstrations. For live web search, set `SEARCH_PROVIDER=brave` and provide `BRAVE_API_KEY`. An unset or unsupported provider is treated as unconfigured rather than silently returning fixture results.

Embeddings are off by default with `EMBEDDING_PROVIDER=none`, so text is not sent to an embedding service and search stays lexical. `local` embeddings require `pip install -r backend/requirements-local-embeddings.txt` and an `EMBEDDING_MODEL`; `openai_compatible` uses the configured embeddings endpoint.

Language-model features are off by default with `LLM_PROVIDER=none`. To enable them, choose `anthropic`, `groq`, or `openai_compatible` and provide the matching key and model configuration. Calls are tracked in the `ai_usage` table and bounded by `LLM_DAILY_CALL_BUDGET`. Keep provider keys in the untracked `.env` only.

## Tests and production notes

Backend tests use isolated database fixtures and do not require a running PostgreSQL instance:

```powershell
Set-Location backend
pytest
```

Build-check the frontend with:

```powershell
Set-Location frontend
npm ci
npm run build
```

Before deployment, use a strong `JWT_SECRET`, non-development database credentials, `ENVIRONMENT=production`, and a deliberate search/provider configuration. Keep `.env` and all API keys out of Git. Production startup rejects insecure secret and database defaults.# Xenom Intel

AI-powered Client / CEO / Competitor / Market Intelligence Platform.

> **XENOM engine.** The architecture in `XENOM_Architecture_Proposal.pdf` is implemented end to end:
> registry -> scheduler/manual run (one pipeline) -> relevance/gate -> storage -> lexical + optional semantic/hybrid
> search -> evidence -> events/timeline -> insights/opportunities/recommendations/alerts/briefings -> claims, with
> versioned profiles, source administration, a multi-label taxonomy, chunks, researcher intelligence and evaluation.
> Embeddings and LLM enrichment are real interfaces that stay **off until configured** (see below); nothing is simulated.

### Optional components (all default to `none`; the engine is fully usable without them)

| Setting | Values | Effect |
|---|---|---|
| `EMBEDDING_PROVIDER` | `none` / `local` / `openai_compatible` | `local` needs `pip install -r backend/requirements-local-embeddings.txt` and `EMBEDDING_MODEL` (for example `sentence-transformers/all-MiniLM-L6-v2`); text never leaves the machine. `openai_compatible` sends chunk text to `EMBEDDING_BASE_URL`. Without a provider, semantic/hybrid search falls back to lexical and says so. |
| `LLM_PROVIDER` | `none` / `anthropic` / `openai_compatible` / `groq` | Borderline adjudication, quote-verified claim extraction, label gaps, cited synthesis and researcher summaries. Every call is logged in `ai_usage`; `LLM_DAILY_CALL_BUDGET` caps calls. `groq` needs `GROQ_API_KEY` (and optionally `GROQ_MODEL`, default `llama-3.3-70b-versatile`); it reuses the same OpenAI-compatible client as `openai_compatible`, just pointed at Groq. |
| `SCHEDULER_ENABLED` | `true` / `false` | Ingestion every 10 minutes; also embeds new chunks when a provider is configured. |

Run order on a new database: `alembic upgrade head`; to index an existing corpus use the explicit admin endpoints
`POST /engine/index/chunks`, `/engine/index/labels`, `/engine/index/embed` (nothing backfills implicitly).
`GET /engine/capabilities` reports what is configured; `python -m evaluation.cli trace-audit` audits provenance.

## What is implemented

- FastAPI backend with an application factory (`app.main:app`).
- Environment-based configuration (`pydantic-settings`), no hardcoded secrets.
- Structured JSON logging + safe global error handling (no stack traces to clients).
- Async SQLAlchemy 2.x engine/session wired to PostgreSQL/pgvector.
- Complete MVP schema as SQLAlchemy models + a reviewable Alembic migration (`0001`).
- `pgcrypto` and `vector` (pgvector) extensions enabled (no vector columns yet).
- Idempotent seed script (dev user, Xenom org, six client placeholders, one
  UNVERIFIED capability).
- `/health` performs a real database connectivity check.

## Prerequisites

- Python 3.12+
- Docker Desktop (for Postgres/pgvector)

## Ports

The Postgres container is published on host port **5433** (mapped to the container's
5432) to avoid colliding with any host-native PostgreSQL on 5432. Inside the compose
network the backend still reaches Postgres at `postgres:5432`.

## Setup (local, host runs)

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create your local env file from the template (never commit `.env`):

```powershell
# from the repo root
Copy-Item .env.example .env
```

Start the database:

```powershell
# from the repo root
docker compose up -d postgres
```

Apply migrations and seed (from `backend`, DATABASE_URL pointing at localhost:5433):

```powershell
cd backend
$env:DATABASE_URL="postgresql+asyncpg://xenom:change_me_local@localhost:5433/xenom_intel"
alembic upgrade head
python -m app.db.seed      # safe to run repeatedly (idempotent)
```

### Local development login

The seed command creates this development-only account for local testing:

```text
Username: dev@xenom.local
Password: development-password
```

This is not a production credential. Set `DEV_USER_PASSWORD` in the uncommitted
`.env` file before seeding any shared or deployed environment.

Run the API:

```powershell
uvicorn app.main:app --reload
```

Check health (expects `db: ok` when the database is reachable):

```json
{
  "status": "ok",
  "service": "xenom-intel-api",
  "version": "0.1.0",
  "db": "ok"
}
```

If the database is unavailable, `/health` returns HTTP 503 with `"db": "error"`
and never exposes connection details or stack traces.

## Run tests

```powershell
cd backend
pytest
```

The tests override the DB session dependency, so they do not require a running
database. Full DB integration is exercised via docker-compose.

## Run with Docker Compose

```powershell
# from the repo root, after creating .env
docker compose up --build -d

# apply migrations / seed inside the running backend container
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.db.seed
```

This starts:
- `postgres` (pgvector/pgvector:pg16) on host port 5433.
- `backend` — the FastAPI service on http://localhost:8000.

## Migrations

Alembic is configured in `backend/alembic.ini` + `backend/alembic/`. The database URL
comes from application settings (`DATABASE_URL`); no credentials live in Alembic config.

```powershell
alembic upgrade head      # apply
alembic downgrade base    # revert
alembic current           # show current revision
```

## Seed data

`python -m app.db.seed` inserts only minimal, safe placeholders and is idempotent:

- one development-only user (`dev@xenom.local`) with a clearly labelled
  placeholder password hash — **not** a real credential;
- a Xenom organization entity;
- the six registered clients (JCB, ACE, Vikas, VIPS, ISM, Definedge) as `ORG`
  entities with `status = needs_review`;
- `CLIENT_OF` relationships (client → Xenom) marked `needs_review`;
- one provisional Xenom capability marked `UNVERIFIED`.

## Frontend and continuous operation

The Vite frontend runs on port `5176` and proxies `/api` to the local API on
port `3000`. The FastAPI lifespan starts the evidence ingestion scheduler every
10 minutes when `SCHEDULER_ENABLED=true`; PM2 can manage the backend using the
repository `ecosystem.config.js` and persists its process list for restart.

No websites, CEOs, competitors, industry facts, capabilities, AI usage, or other
enrichment is invented.

## Configuration

All configuration comes from environment variables / `.env` (see `.env.example`):

| Variable            | Purpose                              |
| ------------------- | ------------------------------------ |
| `APP_NAME`          | Service name reported by `/health`   |
| `APP_VERSION`       | Version reported by `/health`        |
| `ENVIRONMENT`       | `development` / `production`         |
| `LOG_LEVEL`         | Logging level                        |
| `POSTGRES_USER`     | Postgres user                        |
| `POSTGRES_PASSWORD` | Postgres password (local only)       |
| `POSTGRES_DB`       | Postgres database name               |
| `DATABASE_URL`      | Async SQLAlchemy URL (app + Alembic) |

## Not implemented yet (by design)

- Authentication / JWT (M2)
- Any ingestion, entity/event/claim resolution, scoring, opportunities, alerts,
  briefings, RAG, or LLM integration
- Frontend

## Next milestone

**M2 — Auth boundary** (ADMIN/VIEWER roles, local auth, JWT). Not implemented yet.

## Search Engine

The M6/M6+ search layer now supports deterministic fixtures and an optional
Brave web/news provider:

```text
Frontend/API
  -> Search Route
  -> Search Service
  -> Search Provider Interface
  -> Search Provider
  -> Normalized Results
  -> Quality Gate
  -> Relevance Scoring
  -> Deterministic Ranking
  -> Structured Search Response
```

Fixture mode is the default and uses clearly synthetic data. Brave mode is selected
with `SEARCH_PROVIDER=brave` and requires `BRAVE_API_KEY`; keys are read from the
environment and are never returned or logged. Unsupported modes fail safely.

Run the API from `backend` with `SEARCH_PROVIDER=fixture` (the default), then call:

```http
POST /search
Authorization: Bearer <admin-token>
Content-Type: application/json

{"query":"AI robotics","limit":10}
```

The response contains normalized title, URL, snippet, domain, provider, discovery
time, optional publication time, relevance score, and score-signal breakdown.
The quality gate rejects invalid URLs, missing titles/content, and duplicate URLs;
rejection counts are returned explicitly. Scoring is deterministic and transparent:

```text
score = entity_match (30) + topic_match (35) + recency (20)
  + source_quality (10) + content_signal (5) = 100 maximum
```

The current score is a ranking signal, not a factual-truth or confidence claim.
It is intentionally deterministic before semantic or LLM ranking is introduced.

No search results are persisted because the existing article/source schema is for
ingested source content, not transient search responses.

This slice intentionally does not implement semantic search, embeddings, vector
retrieval, LLM ranking, company research, recommendations, alerts, or agents.

## M7 Entity Resolution Slice

The deterministic intelligence flow now continues through canonical entity and
event resolution:

```text
Query -> Search -> Quality/Score -> Page Content -> Evidence Spans
  -> Canonical Entities -> Events -> Explicit Entity-Event Relationships
  -> Evidence Provenance
```

`POST /intelligence/extract` remains the protected read boundary. Fixture mode
merges obvious duplicate mentions using deterministic normalized keys, preserves
the original mentions, and returns only explicitly fixture-declared `INVOLVES`
relationships. Each relationship carries the supporting page evidence, source
URL, provider, timestamps, and search relevance score.

This slice is fully offline, read-only, and synthetic. It does not persist
entities or events, infer speculative relationships, call an LLM, use embeddings,
or add migrations. A later milestone can add reviewed persistence and broader
entity/event resolution.

## Temporal Intelligence Slice

Events now expose deterministic temporal metadata and a timeline:

```text
Evidence -> Event time normalization -> Timeline ordering
         -> Explicit temporal relationships -> Provenance-backed response
```

Fixture event times are marked `exact` or `day` only when explicitly present;
otherwise the event time remains `unknown`. Publication and discovery timestamps
remain separate evidence metadata. Unknown event ordering uses publication time as
a labeled fallback, then discovery time, with the event key as a deterministic tie
breaker. Only fixture-declared `BEFORE`, `AFTER`, or `SAME_TIME` relationships are
returned; no causal or speculative relationship is inferred.

The feature is read-only, synthetic, and fully offline. It does not persist a
timeline or add migrations. A later milestone can support reviewed temporal data
from broader sources.

## Evidence-Backed Claims

`POST /claims` adds deterministic claim creation on top of the fixture-backed
intelligence response. A claim must reference an extracted canonical entity and/or
event, and every evidence item must match existing source URL/page/span provenance.
Claims begin as `unreviewed` and can be moved to `verified` or `rejected` through
the ADMIN-only status endpoint:

```text
POST /claims
PATCH /claims/{claim_id}/status
```

Claim IDs are deterministic for the same statement, references, and evidence, so
repeating a creation request returns the same claim. This slice is intentionally
in-memory and read-only from the database perspective: the existing ORM claim
tables require persisted entity/event/article IDs, while the current intelligence
pipeline returns in-memory canonical keys and evidence. No migration is added.
