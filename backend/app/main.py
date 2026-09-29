from __future__ import annotations

import os
import sys
import threading
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request as UrlRequest, urlopen
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

# Load configuration before importing modules that snapshot timeout/cache
# settings at import time.
BASE_DIR = Path(__file__).resolve().parents[2]
_dotenv_paths = [BASE_DIR / ".env"]
if getattr(sys, "frozen", False):
    # In a PyInstaller build, __file__ points inside a temporary extraction
    # directory. Always also load the user-managed .env beside the EXE.
    _dotenv_paths.insert(0, Path(sys.executable).resolve().parent / ".env")
for _dotenv_path in _dotenv_paths:
    load_dotenv(_dotenv_path, override=False)

from .connectors.registry import CONNECTORS, list_connectors
from .chemistry import structure_svg
from .live_research import live_research
from .llm import relevant_evidence, synthesize
from .hindsight_memory import hindsight_memory
from .models import AuthLoginRequest, AuthRegisterRequest, AssistantRequest, ExperimentCreate, IngestPreviewRequest, LibraryFolderCreate, LibraryItemCreate, SaveLiveChemicalRequest, SimulationRequest, SimulationSaveRequest
from .simulation import simulate_experiment
from .store import Store


store = Store()
if os.getenv("CHEMRD_SEED", "false").lower() not in {"0", "false", "no"}:
    store.seed()

app = FastAPI(title="ChemR&D Intelligence API", version="0.1.0", description="Provenance-aware chemistry R&D MVP")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], allow_credentials=True)

_PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
}


def _session_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return request.cookies.get("chemrd_session")


def _set_session_cookie(response: JSONResponse, token: str, max_age: int) -> None:
    response.set_cookie(
        "chemrd_session",
        token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=os.getenv("CHEMRD_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"},
        path="/",
    )


@app.middleware("http")
async def require_login(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path not in _PUBLIC_API_PATHS:
        user = store.user_for_session(_session_token(request))
        if not user:
            return JSONResponse(status_code=401, content={"detail": "Login required"})
        request.state.user = user
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "chemrd", "database": "sqlite-dev"}


@app.post("/api/auth/register", status_code=201)
def register(request: AuthRegisterRequest):
    try:
        user = store.create_user(request.username, request.password)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    token, max_age = store.create_session(user["id"])
    response = JSONResponse({"user": user, "session_token": token}, status_code=201)
    _set_session_cookie(response, token, max_age)
    return response


@app.post("/api/auth/login")
def login(request: AuthLoginRequest):
    user = store.authenticate_user(request.username, request.password)
    if not user:
        raise HTTPException(401, "Invalid username or password")
    token, max_age = store.create_session(user["id"])
    response = JSONResponse({"user": user, "session_token": token})
    _set_session_cookie(response, token, max_age)
    return response


@app.get("/api/auth/me")
def auth_me(request: Request):
    token = _session_token(request)
    user = store.user_for_session(token)
    if not user:
        raise HTTPException(401, "Login required")
    return {"user": user, "session_token": token}


@app.post("/api/auth/logout")
def logout(request: Request):
    store.delete_session(_session_token(request))
    response = JSONResponse({"ok": True})
    response.delete_cookie("chemrd_session", path="/")
    return response


@app.get("/api/dashboard")
def dashboard():
    return store.dashboard()


@app.get("/api/search")
def search(q: str = Query(min_length=1, max_length=120), live: bool = False):
    response = {"query": q, "results": store.search(q), "live": None}
    if live:
        response["live"] = live_research(q)
        response["results"] += response["live"]["results"]
    return response


@app.get("/api/research/live")
def live_research_endpoint(q: str = Query(min_length=3, max_length=1000)):
    return live_research(q)


@app.get("/api/chemicals")
def chemicals():
    return store.chemicals()


@app.get("/api/chemicals/{chemical_id}")
def chemical(chemical_id: str):
    item = store.chemical(chemical_id)
    if not item:
        raise HTTPException(404, "Chemical not found")
    from .chemistry import valid_smiles
    if not valid_smiles(item.get('smiles')):
        from .live_research import chemical_identity_lookup
        # Earlier builds saved wildcard ontology hits. Recover using the
        # original user-supplied synonym rather than the incorrect class name.
        candidates = [item.get('cas_number'), *item.get('synonyms', []), item.get('name')]
        for candidate in [c for c in candidates if c][:4]:
            fixed, _ = chemical_identity_lookup(candidate)
            if fixed:
                item.update({k: fixed[k] for k in ('name', 'formula', 'molecular_weight', 'smiles', 'inchi', 'inchikey', 'description', 'synonyms') if fixed.get(k) is not None})
                item['structure_svg'] = structure_svg(fixed['smiles'])
                item['properties'] = [{**p, 'value_text': str(p['value']), 'source_title': fixed['source']['title'], 'method': 'RDKit descriptor' if p['origin'] == 'calculated' else 'Provider identity field'} for p in fixed.get('properties', [])]
                break
    item['structure_2d_url'] = f'/api/chemicals/{chemical_id}/structure-2d'
    item['conformer_3d_url'] = f'/api/chemicals/{chemical_id}/conformer-3d'
    return item


def _chemical_structure_asset(chemical_id: str, asset: str) -> Response:
    item = chemical(chemical_id)
    if not item:
        raise HTTPException(404, "Chemical not found")
    return _structure_asset(asset, fallback_smiles=item.get("smiles"), fallback_name=item.get("name"))


def _local_conformer_sdf(smiles: str | None) -> bytes | None:
    from .chemistry import conformer_sdf
    return conformer_sdf(smiles)


def _structure_asset(asset: str, fallback_smiles: str | None = None, fallback_name: str | None = None) -> Response:
    from .structure_service import structure_asset
    try:
        payload, media_type, source = structure_asset(asset, fallback_smiles, fallback_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(content=payload, media_type=media_type, headers={
        "Cache-Control": "private, max-age=86400",
        "X-ChemRD-Structure-Source": source,
        "X-ChemRD-Structure-Origin": "calculated",
    })


@app.get("/api/chemicals/{chemical_id}/structure-2d")
def chemical_structure_2d(chemical_id: str):
    return _chemical_structure_asset(chemical_id, "2d")


@app.get("/api/chemicals/{chemical_id}/conformer-3d")
def chemical_conformer_3d(chemical_id: str):
    return _chemical_structure_asset(chemical_id, "3d")


@app.get("/api/live-structure/2d")
def live_structure_2d(smiles: str | None = Query(default=None), name: str | None = Query(default=None)):
    return _structure_asset("2d", fallback_smiles=smiles, fallback_name=name)


@app.get("/api/live-structure/3d")
def live_structure_3d(smiles: str | None = Query(default=None), name: str | None = Query(default=None)):
    return _structure_asset("3d", fallback_smiles=smiles, fallback_name=name)


@app.get("/api/library/folders")
def library_folders():
    return store.library_folders()


@app.post("/api/library/folders")
def create_library_folder(request: LibraryFolderCreate):
    try:
        return store.create_library_folder(request.name, request.description)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/library/folders/{folder_id}/items")
def library_folder_items(folder_id: str):
    if not any(folder["id"] == folder_id for folder in store.library_folders()):
        raise HTTPException(404, "Library folder not found")
    return store.library_items(folder_id)


@app.post("/api/library/items")
def create_library_item(request: LibraryItemCreate):
    try:
        return store.create_library_item(request.model_dump())
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/library/chemicals")
def save_live_chemical(request: SaveLiveChemicalRequest):
    try:
        return store.save_live_chemical(request.record, request.folder_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/sources")
def sources():
    return store.sources()


@app.get("/api/experiments")
def experiments():
    return store.experiments()


@app.post("/api/experiments")
def create_experiment_endpoint(request: ExperimentCreate, http_request: Request):
    try:
        payload = request.model_dump()
        payload["user_id"] = http_request.state.user["id"]
        return store.create_experiment(payload)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/experiments/simulate")
def simulate_experiment_endpoint(request: SimulationRequest):
    try:
        return simulate_experiment(request)
    except Exception as exc:
        raise HTTPException(500, f"Simulation failed: {exc}") from exc


@app.get("/api/simulations")
def simulations(request: Request):
    return store.simulations(request.state.user["id"])


@app.get("/api/simulations/{simulation_id}")
def simulation_detail(simulation_id: str, request: Request):
    record = store.simulation(request.state.user["id"], simulation_id)
    if not record:
        raise HTTPException(404, "Simulation not found")
    return record


@app.post("/api/simulations", status_code=201)
def save_simulation(request: SimulationSaveRequest, http_request: Request):
    return store.create_simulation(http_request.state.user["id"], request.model_dump())


@app.get("/api/formulations")
def formulations():
    return store.formulations()


@app.get("/api/reactions")
def reactions():
    return store.reactions()


@app.get("/api/analyses")
def analyses():
    return store.analyses()


@app.get("/api/analyses/{analysis_id}")
def analysis(analysis_id: str):
    item = store.analysis(analysis_id)
    if not item:
        raise HTTPException(404, "Analysis not found")
    return item


@app.get("/api/plots/chemical-properties")
def chemical_property_plot_catalog():
    return store.chemical_property_catalog()


@app.get("/api/graph")
def graph():
    return store.graph()


@app.get("/api/ingest/connectors")
def connectors():
    return list_connectors()


@app.post("/api/ingest/preview")
def ingest_preview(request: IngestPreviewRequest):
    connector = CONNECTORS.get(request.connector_id)
    if not connector:
        raise HTTPException(404, "Connector not found")
    return connector.preview(request.payload)


@app.get("/api/assistant/history")
def assistant_history(request: Request):
    user = getattr(request.state, "user", None) or {}
    return {"items": store.assistant_history(user["id"])}


@app.delete("/api/assistant/history")
def clear_assistant_history(request: Request):
    user = getattr(request.state, "user", None) or {}
    store.clear_assistant_history(user["id"])
    return {"ok": True}


@app.post("/api/assistant")
def assistant(request: AssistantRequest, http_request: Request):
    user = getattr(http_request.state, "user", None) or {}
    memory = hindsight_memory.recall(user.get("id", ""), request.question)

    def remember(answer: str, source_count: int = 0) -> None:
        if not answer or not user.get("id") or not hindsight_memory.enabled():
            return
        threading.Thread(
            target=hindsight_memory.retain_turn,
            args=(user["id"], request.question, answer, source_count),
            daemon=True,
        ).start()

    def record_history(answer: str, citations: list[dict], assistant_status: str, provider: str | None, memory_status: str) -> None:
        if user.get("id") and request.question.strip() and answer.strip():
            store.add_assistant_history(
                user["id"],
                request.question,
                answer,
                citations,
                assistant_status,
                provider,
                memory_status,
            )

    if request.live:
        live = live_research(request.question)
        evidence = relevant_evidence(request.question, live)
        evidence["memory_context"] = memory.get("items", [])
        synthesis = synthesize(request.question, evidence)
        if len(synthesis) == 3:
            generated, llm_error, llm_provider = synthesis
        else:  # compatibility with lightweight test doubles and older integrations
            generated, llm_error = synthesis
            llm_provider = None
        if not live.get("providers") and not live.get("results"):
            assistant_status = "Needs a specific subject"
            assistant_mode = "retrieval_summary"
        elif generated:
            assistant_status = f"{llm_provider.title() if llm_provider else 'AI'} synthesis active"
            assistant_mode = "llm_synthesis"
        elif llm_error and "RateLimitError" in llm_error:
            assistant_status = "AI providers rate-limited; showing relevant retrieval"
            assistant_mode = "retrieval_summary"
        elif llm_error and "AuthenticationError" in llm_error:
            assistant_status = "AI providers rejected their keys; showing relevant retrieval"
            assistant_mode = "retrieval_summary"
        elif llm_error:
            assistant_status = "AI providers unavailable; showing relevant retrieval"
            assistant_mode = "retrieval_summary"
        else:
            assistant_status = "Relevant cited retrieval"
            assistant_mode = "retrieval_summary"
        answer = generated or evidence["answer"]
        memory_status = memory.get("status", hindsight_memory.status())
        record_history(answer, evidence.get("citations", []), assistant_status, llm_provider, memory_status)
        remember(answer, len(evidence.get("citations", [])))
        return {
            "answer": answer,
            "citations": evidence.get("citations", []),
            "results": evidence.get("results", []),
            "providers": live.get("providers", {}),
            "retrieved_at": live.get("retrieved_at"),
            "assistant_mode": assistant_mode,
            "assistant_provider": llm_provider,
            "assistant_status": assistant_status,
            "evidence_quality": evidence.get("evidence_quality", "none"),
            "memory_enabled": hindsight_memory.enabled(),
            "memory_used": bool(memory.get("items")),
            "memory_status": memory_status,
            "disclaimer": "General answers come from the configured AI provider when no relevant source was retrieved. Verify scientific, safety, regulatory, and process claims against primary sources before relying on them.",
        }
        # Do not replace a clear live-retrieval response with unrelated seeded
        # demo content when the query is ambiguous or has no live match.
        answer = live["answer"]
        memory_status = memory.get("status", hindsight_memory.status())
        record_history(answer, live.get("citations", []), "Needs a specific subject" if not live.get("providers") else "No live matches", None, memory_status)
        remember(answer, len(live.get("citations", [])))
        return {
            "answer": answer,
            "citations": live.get("citations", []),
            "results": live.get("results", []),
            "providers": live.get("providers", {}),
            "retrieved_at": live.get("retrieved_at"),
            "assistant_mode": "retrieval_summary",
            "assistant_provider": None,
            "assistant_status": "Needs a specific subject" if not live.get("providers") else "No live matches",
            "memory_enabled": hindsight_memory.enabled(),
            "memory_used": bool(memory.get("items")),
            "memory_status": memory_status,
            "disclaimer": "Live retrieval uses documented public APIs. Verify the cited primary source and applicable license before relying on a claim.",
        }
    chemical = store.chemical(request.chemical_id) if request.chemical_id else None
    q = request.question.lower()
    citations = []
    if chemical:
        citations.append({"id": "src-chemical-identity-demo", "label": "Chemical identity reference", "reason": "chemical identifiers and formula"})
        citations.append({"id": "src-lab-thermal", "label": "Pilot thermal run TGA-024", "reason": "internal measured thermal observations"})
    else:
        citations.append({"id": "src-journal-phenolic", "label": "Phenolic resin review", "reason": "literature context"})
        citations.append({"id": "src-lab-thermal", "label": "Pilot thermal run TGA-024", "reason": "measured demo data"})
    if "thermal" in q or "tga" in q or "char" in q:
        answer = "The seeded thermal screen suggests the FR-Resole pilot retains more char at 650–850 °C than the resole control. Treat the measured curves as experiment-specific; the model-predicted flame-retardancy index is a separate screening signal and is not blended into the measured result."
    elif chemical:
        answer = f"{chemical['name']} is represented with explicit identifiers and {len(chemical['properties'])} provenance-tagged property values. The record keeps measured, extracted, calculated, predicted, and AI-estimated origins separate so you can compare them without silently merging evidence."
    else:
        answer = "I found a useful starting point in the seeded phenolic/resole dataset. Ask about a named chemical, a thermal technique, or a formulation and I will keep the answer tied to the cited records."
    remember(answer, len(citations))
    memory_status = memory.get("status", hindsight_memory.status())
    record_history(answer, citations, "Workspace evidence", None, memory_status)
    return {
        "answer": answer,
        "citations": citations,
        "memory_enabled": hindsight_memory.enabled(),
        "memory_used": bool(memory.get("items")),
        "memory_status": memory_status,
        "disclaimer": "Demo assistant response; verify against primary records before making a process or safety decision.",
    }


frontend_dir = BASE_DIR / "frontend"
if frontend_dir.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        candidate = frontend_dir / path
        if path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(frontend_dir / "index.html")
