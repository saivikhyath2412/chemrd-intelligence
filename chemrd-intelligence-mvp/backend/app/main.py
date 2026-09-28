from __future__ import annotations

import os
import sys
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
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
from .live_research import live_research
from .llm import synthesize
from .models import AssistantRequest, IngestPreviewRequest, LibraryFolderCreate, LibraryItemCreate, SaveLiveChemicalRequest
from .store import Store


store = Store()
if os.getenv("CHEMRD_SEED", "false").lower() not in {"0", "false", "no"}:
    store.seed()

app = FastAPI(title="ChemR&D Intelligence API", version="0.1.0", description="Provenance-aware chemistry R&D MVP")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "chemrd", "database": "sqlite-dev"}


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
    return item


def _pubchem_asset(chemical_id: str, asset: str) -> Response:
    item = store.chemical(chemical_id)
    cid = item.get("pubchem_cid") if item else None
    if not cid:
        raise HTTPException(404, "No public conformer is linked to this chemical")
    return _pubchem_asset_by_cid(
        str(cid),
        asset,
        fallback_smiles=item.get("smiles") if item else None,
        fallback_name=item.get("name") if item else None,
    )


def _local_conformer_sdf(smiles: str | None) -> bytes | None:
    """Generate a real 3D SDF when RDKit is installed in the deployment."""
    if not smiles:
        return None
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem

        mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
        if mol is None:
            return None
        params = AllChem.ETKDGv3()
        params.randomSeed = 0xC0FFEE
        params.useRandomCoords = True
        if AllChem.EmbedMolecule(mol, params) < 0:
            return None
        try:
            AllChem.MMFFOptimizeMolecule(mol, maxIters=200)
        except Exception:
            AllChem.UFFOptimizeMolecule(mol, maxIters=200)
        return (Chem.MolToMolBlock(Chem.RemoveHs(mol)) + "\n$$$$\n").encode("utf-8")
    except Exception:
        return None


def _pubchem_asset_by_cid(
    cid: str,
    asset: str,
    fallback_smiles: str | None = None,
    fallback_name: str | None = None,
) -> Response:
    if asset == "2d":
        candidates = [
            (f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG?image_size=large", "image/png", "PubChem 2-D depiction"),
            (f"https://cactus.nci.nih.gov/chemical/structure/{quote(fallback_name or cid, safe='')}/image", "image/png", "NCI Cactus 2-D depiction"),
        ]
    else:
        candidates = []
        # PubChem intentionally has no generated 3-D conformer for some large
        # molecules. Paclitaxel is one of those records. NIH 3D publishes a
        # public-domain, workflow-generated MOL file for it, so use that as a
        # source-backed fallback before trying general structure services.
        normalized_name = (fallback_name or "").strip().casefold()
        if str(cid) == "36314" or normalized_name in {"paclitaxel", "taxol"}:
            candidates.append(
                (
                    "https://3d.nih.gov/api/files/94543",
                    "chemical/x-mdl-sdfile",
                    "NIH 3D 3DPX-003114 (public-domain workflow-generated conformer)",
                )
            )
        candidates.extend(
            [
                (f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/SDF?record_type=3d", "chemical/x-mdl-sdfile", "PubChem 3-D conformer"),
                (f"https://cactus.nci.nih.gov/chemical/structure/{quote(fallback_name or cid, safe='')}/file?format=sdf&get3d=true", "chemical/x-mdl-sdfile", "NCI Cactus generated conformer"),
            ]
        )
    last_error = None
    for candidate in candidates:
        url, media_type, source_label = candidate
        try:
            request = Request(url, headers={"User-Agent": "ChemRD-Intelligence/0.1"})
            with urlopen(request, timeout=25) as response:
                payload = response.read()
            # NIH 3D's official molecule file is a MOL file without the SDF
            # record terminator. Add it so every downstream SDF consumer,
            # including 3Dmol.js, receives a complete single-record SDF.
            if media_type == "chemical/x-mdl-sdfile" and b"M  END" in payload and b"$$$$" not in payload:
                payload += b"\n$$$$\n"
            return Response(
                content=payload,
                media_type=media_type,
                headers={
                    "Cache-Control": "public, max-age=86400",
                    "X-ChemRD-Structure-Source": source_label,
                    "X-ChemRD-Structure-Origin": "model_predicted",
                },
            )
        except (OSError, URLError) as exc:
            last_error = exc
    if asset == "3d":
        local_sdf = _local_conformer_sdf(fallback_smiles)
        if local_sdf:
            return Response(
                content=local_sdf,
                media_type="chemical/x-mdl-sdfile",
                headers={
                    "Cache-Control": "private, max-age=3600",
                    "X-ChemRD-Structure-Source": "Local RDKit ETKDG/MMFF conformer",
                    "X-ChemRD-Structure-Origin": "calculated",
                },
            )
    raise HTTPException(502, f"The external structure service is unavailable: {last_error}")


@app.get("/api/chemicals/{chemical_id}/structure-2d")
def chemical_structure_2d(chemical_id: str):
    return _pubchem_asset(chemical_id, "2d")


@app.get("/api/chemicals/{chemical_id}/conformer-3d")
def chemical_conformer_3d(chemical_id: str):
    return _pubchem_asset(chemical_id, "3d")


@app.get("/api/pubchem/{cid}/structure-2d")
def pubchem_structure_2d(cid: str):
    if not cid.isdigit():
        raise HTTPException(400, "PubChem CID must be numeric")
    return _pubchem_asset_by_cid(cid, "2d")


@app.get("/api/pubchem/{cid}/conformer-3d")
def pubchem_conformer_3d(cid: str, smiles: str | None = Query(default=None), name: str | None = Query(default=None)):
    if not cid.isdigit():
        raise HTTPException(400, "PubChem CID must be numeric")
    return _pubchem_asset_by_cid(cid, "3d", fallback_smiles=smiles, fallback_name=name)


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


@app.post("/api/assistant")
def assistant(request: AssistantRequest):
    if request.live:
        live = live_research(request.question)
        if live["results"]:
            synthesis = synthesize(request.question, live)
            if len(synthesis) == 3:
                generated, llm_error, llm_provider = synthesis
            else:  # compatibility with lightweight test doubles and older integrations
                generated, llm_error = synthesis
                llm_provider = None
            if generated:
                assistant_status = f"{llm_provider.title() if llm_provider else 'AI'} synthesis active"
            elif llm_error and "RateLimitError" in llm_error:
                assistant_status = "AI providers rate-limited; showing cited retrieval"
            elif llm_error and "AuthenticationError" in llm_error:
                assistant_status = "AI providers rejected their keys; showing cited retrieval"
            elif llm_error:
                assistant_status = "AI providers unavailable; showing cited retrieval"
            else:
                assistant_status = "Cited retrieval"
            return {
                "answer": generated or live["answer"],
                "citations": live["citations"],
                "results": live["results"],
                "providers": live["providers"],
                "retrieved_at": live["retrieved_at"],
                "assistant_mode": "llm_synthesis" if generated else "retrieval_summary",
                "assistant_provider": llm_provider,
                "assistant_status": assistant_status,
                "disclaimer": "Live retrieval uses documented public APIs. Verify the cited primary source and applicable license before relying on a claim.",
            }
        # Do not replace a clear live-retrieval response with unrelated seeded
        # demo content when the query is ambiguous or has no live match.
        return {
            "answer": live["answer"],
            "citations": live.get("citations", []),
            "results": live.get("results", []),
            "providers": live.get("providers", {}),
            "retrieved_at": live.get("retrieved_at"),
            "assistant_mode": "retrieval_summary",
            "assistant_provider": None,
            "assistant_status": "Needs a specific subject" if not live.get("providers") else "No live matches",
            "disclaimer": "Live retrieval uses documented public APIs. Verify the cited primary source and applicable license before relying on a claim.",
        }
    chemical = store.chemical(request.chemical_id) if request.chemical_id else None
    q = request.question.lower()
    citations = []
    if chemical:
        citations.append({"id": "src-pubchem-demo", "label": "Identity reference", "reason": "chemical identifiers and formula"})
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
    return {"answer": answer, "citations": citations, "disclaimer": "Demo assistant response; verify against primary records before making a process or safety decision."}


frontend_dir = BASE_DIR / "frontend"
if frontend_dir.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        candidate = frontend_dir / path
        if path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(frontend_dir / "index.html")
