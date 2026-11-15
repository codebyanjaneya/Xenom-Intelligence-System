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

## Optional components

All default to off; the deterministic engine is fully usable without them.

| Setting | Values | Effect |
|---|---|---|
| `EMBEDDING_PROVIDER` | `none` / `local` / `openai_compatible` | `local` needs `backend/requirements-local-embeddings.txt` and `EMBEDDING_MODEL` (text stays on the machine). `openai_compatible` sends chunk text to `EMBEDDING_BASE_URL`. Without a provider, semantic/hybrid search falls back to lexical and says so in `fallback_reason`. |
| `LLM_PROVIDER` | `none` / `anthropic` / `openai_compatible` / `groq` | Borderline relevance adjudication (queued for review), quote-verified claim extraction, label gaps, cited synthesis, researcher summaries and the copilot. Every call is logged in `ai_usage`; `LLM_DAILY_CALL_BUDGET` caps the number of calls per day. `groq` needs `GROQ_API_KEY` (optional `GROQ_MODEL`, default `openai/gpt-oss-120b`). |
| `SCHEDULER_ENABLED` | `true` / `false` | In-process ingestion cycle every 10 minutes (one instance at a time via a PostgreSQL advisory lock); also embeds new chunks when a provider is configured. |

To index an existing corpus use the explicit admin endpoints `POST /engine/index/chunks`, `/engine/index/labels` and
`/engine/index/embed` (nothing backfills implicitly). `GET /engine/capabilities` reports what is configured, and
`python -m evaluation.cli trace-audit` checks that every evidence span resolves to stored text.

## Access control

- Two roles: `ADMIN` (all clients, all write, ingestion and LLM operations) and `VIEWER` (read-only).
- Client data is **default-deny** for non-admin users: a viewer sees only the clients an administrator granted with
  `PUT /auth/users/{user_id}/clients` (`{"clients": ["JCB", "ACE"]}`). With no grant, a viewer sees no client data;
  search results still include records not attributed to any client (research and technology items).
- Outbound fetches of user-, feed- or source-supplied URLs go through an SSRF guard (`app/core/net_safety.py`): only
  http(s), every resolved address must be public (private, loopback, link-local/metadata, CGNAT and reserved ranges
  are refused), every redirect hop is re-checked, and response bodies are size-capped. Configured LLM/embedding
  endpoints and the fixed research/news APIs are not routed through the guard (a local model may legitimately be on
  localhost).

## Tests

The backend tests are **integration tests against a real, migrated PostgreSQL database with pgvector**. Without that
database every test is skipped. They refuse to run against any database whose name does not end in `_test`, because
they delete rows between tests.

```powershell
# create an empty xenom_intel_test database (pgvector image from docker-compose), then from backend/:
$env:DATABASE_URL="postgresql+asyncpg://xenom:<password>@localhost:5433/xenom_intel_test"
alembic upgrade head
$env:TEST_DATABASE_URL=$env:DATABASE_URL
pytest
```

External HTTP calls and language models are **mocked** in the automated tests (`httpx.MockTransport`, scripted fake
providers in `backend/tests/ai_fakes.py`). The tests verify the plumbing and guardrails (quote verification, citation
filtering, budget, fallbacks, authorization, SSRF refusals); they do not measure real model quality.

`alembic check` should report no pending operations; ORM models declare the generated search columns, the pgvector
column and their indexes so autogenerate never proposes dropping them.

Build-check the frontend with:

```powershell
Set-Location frontend
npm ci
npm run build
```

There are no automated frontend tests yet.

## Evaluation

`backend/evaluation/` contains a labelling and metrics harness (relevance, search and event quality) with seeded,
stratified samples. The label files are not filled in yet, so no precision/recall figures exist; the CLI reports
"not measured" until labels are added.

## Status and production notes

XENOM is a working local pilot. It runs against live sources on a developer machine; it has not been deployed as a
production service. Known gaps before any deployment include: no CI pipeline, no production serving of the frontend,
token revocation and login throttling held in process memory, no API rate limiting beyond login, no retry or
fallback provider for LLM calls, and an untyped vector column without an ANN index.

If you deploy it anyway, use a strong `JWT_SECRET`, non-development database credentials, `ENVIRONMENT=production`
and a deliberate search/provider configuration, and keep `.env` and all API keys out of Git. Production startup
rejects the placeholder JWT secret, the development database password and the fixture search provider.
`ecosystem.config.js` is a Windows development convenience (it runs the Vite dev server), not a production setup.
