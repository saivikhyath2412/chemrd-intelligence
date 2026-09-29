# Application architecture

## Runtime

ChemR&D is a single-origin application: FastAPI serves both the static frontend and JSON API. The Windows launcher binds the server to loopback (`127.0.0.1`) on an available port and opens it in a native WebView2 window. The packaged app keeps its SQLite database under `%LOCALAPPDATA%\ChemRD`, outside PyInstaller's temporary extraction directory. API integrations are optional and configured outside the executable through a user-managed `.env` beside the executable.

## Component boundaries

| Area | Responsibility |
| --- | --- |
| `backend/app/main.py` | HTTP routes, authenticated request context, and orchestration between storage, chemistry, research, AI, and simulation services. |
| `backend/app/store.py` | Persistence boundary for users, sessions, preferences, chemical records, assistant history, and experiments. Queries use bound parameters; passwords are salted PBKDF2 hashes. |
| `backend/app/live_research.py`, `connectors/` | Normalized live/public-source lookups and connector contracts. Source metadata and value provenance remain explicit. |
| `backend/app/llm.py` | AI provider selection/fallback, compact prompts, source relevance filtering, and separation of cited evidence from private continuity memory. |
| `backend/app/hindsight_memory.py`, `memory_privacy.py` | Optional external memory adapter and credential redaction. See [Hindsight memory](HINDSIGHT_MEMORY.md) for its data lifecycle and limitations. |
| `backend/app/simulation.py`, `chemistry.py`, `structure_service.py` | Domain calculations, chemical identity/structure processing, and simulation support. |
| `frontend/assets/app.js` | SPA navigation, view rendering, API calls, and user interactions. |
| `frontend/assets/styles.css`, `assistant-chat-scroll.css` | Shared visual system and scoped Research Assistant viewport/scroll behavior. |
| `tests/` | API, domain, privacy, provider-fallback, structure, and simulation regression coverage. |

## Security boundaries

- The packaged server listens only on loopback. The UI calls its API on the same origin; the app does not grant arbitrary credentialed cross-origin access.
- API keys are loaded by the backend from environment configuration and are not embedded in frontend bundles. `.env` is ignored by Git; commit only `.env.example` with empty values.
- Session tokens are stored hashed in the database and issued in an HttpOnly, SameSite cookie. The desktop SPA also keeps the returned bearer token in `sessionStorage` for authenticated API requests; it is cleared on logout. Passwords are stored as salted PBKDF2 hashes, never plaintext.
- Chemical provider values, local calculations, literature values, model estimates, and assistant memory have separate origins/roles in the data model and UI.

## Local quality checks

Run from the repository root:

```powershell
pytest -q
node --check frontend/assets/app.js
python -m pip check
```

Build the Windows one-file app using the desktop optional dependencies and `chemrd-desktop.spec` as described in the root README. Keep generated EXEs, archives, temporary build folders, and real `.env` files out of source commits; publish tested binaries as GitHub Release assets instead.
