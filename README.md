# ChemR&D Intelligence Platform

ChemR&D is a runnable local MVP for chemistry and materials R&D teams. It combines a provenance-aware FastAPI backend with a polished single-page frontend. New workspaces start clean; optional demo data can be enabled explicitly for demonstrations.

## What is included

- Universal search across chemicals, experiments, reactions, papers, patents, and formulations.
- Live open-ended research mode through documented public APIs (NCI/CADD Cactus, OPSIN, ChEBI, OpenAlex, Crossref, Europe PMC) with provider status and citations.
- In-app source reader for live results, with an embedded browser panel and external fallback when a publisher blocks iframe embedding.
- Chemical records with identifiers, SMILES, formula, properties, provenance, and an optional RDKit renderer.
- Chemical Library folders that can hold saved chemicals plus papers, patents, websites, notes, experiments, files, and other research items.
- 2D structure cards and an interactive 3D molecular viewer with a 3Dmol.js enhancement when the library is available.
- Experiments, formulations, reactions/process records, and source/provenance metadata.
- Analytical workspace for TGA/DTG, DSC, FTIR, NMR, XRD, and generic chart data with comparison overlays.
- Knowledge graph linking chemicals, properties, sources, experiments, and reactions.
- Research assistant with compact evidence prompts and provider fallback: OpenAI → Gemini → Groq.
- Ingestion connector interfaces for lawful/open/licensed sources; no internet scraping is included.
- Explicit value origins: `measured`, `literature_extracted`, `calculated`, `model_predicted`, and `ai_estimated`.
- SQLite for zero-setup local development; PostgreSQL + pgvector is the production-shaped option.
- Username/password registration and login with salted PBKDF2 password hashes and persistent HttpOnly sessions.
- Optional Hindsight memory integration for private assistant continuity, with strict per-user memory scoping and no memory facts treated as research citations.

## Quick start

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn backend.app.main:app --reload
```

Open http://127.0.0.1:8000. Interactive API docs are at http://127.0.0.1:8000/docs.

The assistant and Universal Search can query live public sources. Enable “Search documented public APIs live” in Universal Search or “Search live public sources” in the assistant. The API equivalents are `GET /api/research/live?q=...`, `GET /api/search?q=...&live=true`, and `POST /api/assistant` with `{ "question": "...", "live": true }`.

On first launch, register an account in the login screen. The account and session records are stored in the configured database; the password itself is never stored. The browser session is held in an HttpOnly cookie. `CHEMRD_SESSION_DAYS` controls session lifetime and defaults to 30 days. The desktop app keeps its SQLite database under `%LOCALAPPDATA%\\ChemRD`.

### Optional Hindsight memory

The Research Assistant can use Hindsight for private conversation continuity. It recalls user-scoped memories before AI synthesis and queues the completed question/answer for retention afterward. Hindsight memory is deliberately kept separate from live research evidence and is never emitted as a citation. Hindsight is disabled by default; enable it in `.env` with `HINDSIGHT_ENABLED=true`. For Hindsight Cloud, set `HINDSIGHT_API_URL=https://api.hindsight.vectorize.io` and `HINDSIGHT_API_KEY=hsk_...`. A self-hosted Hindsight server can use its local URL instead, such as `http://localhost:8888`; the API key may be left empty when the server does not require authentication. The integration uses Hindsight’s documented `retain` and `recall` endpoints and continues normally if Hindsight is offline.

Live chemical identity cards and assistant identity answers include a **Save to chemical library** action. The folder picker lets a user save the canonical chemical record or any related research item into a chosen folder. Folder contents are separate from the canonical chemical table, so a folder can organize mixed research material without changing chemical identity or provenance.

## Chemical identity and structure providers

The legacy NCBI provider is not required by this build. Live chemical lookups use the following provider stack:

- NCI/CADD Chemical Identifier Resolver (Cactus) for names, CAS/registry identifiers, SMILES, InChI, InChIKey, synonyms, 2-D depictions, and generated SDF conformers.
- OPSIN as a fallback for systematic chemical names and IUPAC-style name-to-structure conversion.
- ChEBI as a curated small-molecule fallback with identifiers, synonyms, formula, mass, and structure records.
- RDKit, when installed, for locally calculated formula, mass, logP, TPSA, hydrogen-bond counts, rotatable bonds, and 2-D/3-D fallbacks.

Provider-returned identity, locally calculated descriptors, and experimental/literature values remain separate in the property ledger. The relevant API routes are `/api/research/live`, `/api/live-structure/2d`, `/api/live-structure/3d`, `/api/chemicals/{chemical_id}/structure-2d`, and `/api/chemicals/{chemical_id}/conformer-3d`.

### Windows desktop executable

The repository includes `desktop_launcher.py` and `chemrd-desktop.spec`. The executable uses a native WebView2 window; it does not open Chrome or Edge. Windows 10/11 should have the Microsoft Edge WebView2 Runtime installed. After installing the desktop extras, build the app with:

```powershell
pip install -e ".[desktop]"
pyinstaller --clean --noconfirm chemrd-desktop.spec
```

The executable will be created at `dist/ChemRD-Intelligence.exe`. Double-clicking it starts the local server, chooses an available local port, and opens the app in a native window. Clicking a live citation opens the cited page in a second native ChemR&D window, not an external browser tab. Its SQLite database is stored under `%LOCALAPPDATA%\\ChemRD`.

If Python is not on PATH, use any Python 3.11+ interpreter and run the same commands with that interpreter.

### Optional PostgreSQL profile

```powershell
docker compose up -d db
$env:DATABASE_URL = "postgresql+psycopg://chemrd:chemrd@localhost:5432/chemrd"
uvicorn backend.app.main:app --reload
```

The application keeps the local store deliberately small and dependency-light. `backend/app/store.py` is the adapter boundary; replace its SQLite implementation with SQLAlchemy/pgvector repositories when deploying at scale. `migrations/001_init.sql` documents the PostgreSQL-shaped schema and vector extension hook.

## Configuration

Copy `.env.example` to `.env` if desired:

For the source version, keep `.env` in the repository root beside `pyproject.toml`. For the Windows executable, keep `.env` beside `ChemRD-Intelligence.exe`. The file is ignored by Git and should never be committed.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./chemrd.db` | SQLite or PostgreSQL connection target |
| `CHEMRD_DATA_DIR` | `./data` | Local uploads and connector workspace |
| `CHEMRD_SEED` | `false` | Seed demo chemistry on first startup only when explicitly enabled |
| `CHEMRD_CLEAR_SEED_DATA` | `true` in the desktop build | Remove the known built-in demo rows when running in clean mode |
| `CHEMRD_DEMO_MODE` | `false` | Explicitly enable the optional built-in demo workspace |
| `CHEMRD_PUBLIC_DATA_POLICY` | `lawful_open_or_licensed` | Connector governance label |
| `CHEMRD_LIVE_TIMEOUT` | `8` | Seconds per public API request |
| `LLM_PROVIDER_ORDER` | `openai,gemini,groq` | Research-assistant provider order |
| `OPENAI_API_KEY` | empty | OpenAI Responses API key |
| `GEMINI_API_KEY` | empty | Google Gemini API key |
| `GROQ_API_KEY` | empty | Groq Cloud API key |
| `HINDSIGHT_ENABLED` | `false` | Enable optional Research Assistant memory |
| `HINDSIGHT_API_URL` | empty | Hindsight Cloud or self-hosted API base URL |
| `HINDSIGHT_API_KEY` | empty | Hindsight Cloud token; never commit it |
| `HINDSIGHT_BANK_PREFIX` | `chemrd` | Prefix for per-user Hindsight memory banks |
| `HINDSIGHT_RECALL_BUDGET` | `low` | Hindsight recall depth: `low`, `mid`, or `high` |
| `HINDSIGHT_RECALL_MAX_TOKENS` | `1200` | Maximum private memory context sent to the assistant |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Groq model ID |
| `CHEMRD_ASSISTANT_MAX_SOURCES` | `7` | Maximum evidence records sent to a model |
| `CHEMRD_ASSISTANT_ABSTRACT_CHARS` | `900` | Maximum characters per paper abstract in the prompt |
| `CHEMRD_ASSISTANT_MAX_OUTPUT_TOKENS` | `1200` | Output cap per model call |
| `CHEMRD_LIVE_CACHE_TTL` | `600` | Seconds to reuse an identical public-source search |

## Testing

```powershell
pytest -q
```

The tests cover health, search, provenance/value-origin separation, analysis overlays, graph data, and ingestion connector discovery.

## Project layout

```text
backend/app/       FastAPI app, store, seed data, chemistry helpers, connectors
frontend/          static SPA assets served by FastAPI
migrations/        PostgreSQL/pgvector reference schema
tests/             API and domain tests
```

## Data and provenance model

Every research value is stored in `property_values` with a required `origin` and optional `source_id`, method, confidence, and notes. A measured value can sit beside a literature-extracted or model-predicted value without overwriting it. The UI labels origins everywhere they are shown and the API returns the full provenance object.

## Connector policy

Connectors are intentionally interfaces, not scrapers. A connector declares its source, license/policy, and capabilities, and returns normalized records from an approved open, licensed, or local source. Add a connector under `backend/app/connectors/` and register it in `registry.py`.

Live retrieval uses documented public APIs instead of copying the open web. It returns metadata and abstracts when providers make them available, links to the original source, and preserves source/license context. Full text can remain paywalled, blocked, or restricted by the publisher; the app does not bypass those controls.

## Production hardening roadmap

1. Move repository methods to SQLAlchemy 2 + PostgreSQL/pgvector and add migrations through Alembic.
2. Add authentication, workspace-level authorization, object storage, audit logs, and background ingestion jobs.
3. Add licensed registry, CAS, patent, and vendor connectors with rate limits and source-specific attribution.
4. Add RDKit standardization, descriptors, substructure search, and embedding generation.
5. Replace the demo assistant with a retrieval pipeline that filters by source license and cites retrieved records.
