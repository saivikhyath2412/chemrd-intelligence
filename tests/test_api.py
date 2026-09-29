import os
import json
import uuid
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test-chemrd.db"
os.environ["CHEMRD_SEED"] = "true"

from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.main import app, store


client = TestClient(app)

# The API is session-protected. Keep the existing tests focused on application
# behavior by giving their shared client one disposable authenticated session.
_test_username = f"test-suite-{uuid.uuid4().hex[:10]}"
_auth_bootstrap = client.post(
    "/api/auth/register",
    json={"username": _test_username, "password": "test-password-123"},
)
assert _auth_bootstrap.status_code == 201, _auth_bootstrap.text


def teardown_module():
    if Path("test-chemrd.db").exists():
        Path("test-chemrd.db").unlink()


def test_auth_register_login_me_logout():
    auth_client = TestClient(app)
    username = f"auth-{uuid.uuid4().hex[:10]}"
    registered = auth_client.post(
        "/api/auth/register",
        json={"username": username, "password": "strong-pass-123"},
    )
    assert registered.status_code == 201
    assert registered.json()["user"]["username"] == username
    assert registered.json()["session_token"]
    assert auth_client.get("/api/auth/me").status_code == 200

    bearer_client = TestClient(app)
    bearer = registered.json()["session_token"]
    assert bearer_client.get("/api/dashboard", headers={"Authorization": f"Bearer {bearer}"}).status_code == 200

    assert auth_client.post("/api/auth/logout").status_code == 200
    assert auth_client.get("/api/auth/me").status_code == 401

    logged_in = auth_client.post(
        "/api/auth/login",
        json={"username": username, "password": "strong-pass-123"},
    )
    assert logged_in.status_code == 200
    assert logged_in.json()["user"]["username"] == username
    assert auth_client.post(
        "/api/auth/login",
        json={"username": username, "password": "wrong-password"},
    ).status_code == 401


def test_health_and_dashboard():
    assert client.get("/api/health").json()["status"] == "ok"
    payload = client.get("/api/dashboard").json()
    assert payload["counts"]["chemicals"] >= 5
    assert {item["origin"] for item in payload["origin_counts"]} >= {"measured", "calculated", "ai_estimated"}


def test_assistant_history_is_persistent_and_user_scoped():
    user = client.get("/api/auth/me").json()["user"]
    created = store.add_assistant_history(
        user["id"],
        "What is paclitaxel?",
        "Paclitaxel answer",
        [{"label": "Chemical identity record"}],
        "Groq synthesis active",
        "groq",
        "ready",
    )
    response = client.get("/api/assistant/history")
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == created["id"]
    assert response.json()["items"][0]["question"] == "What is paclitaxel?"
    assert response.json()["items"][0]["citations"][0]["label"] == "Chemical identity record"
    assert client.delete("/api/assistant/history").json()["ok"] is True
    assert client.get("/api/assistant/history").json()["items"] == []


def test_search_and_chemical_provenance():
    results = client.get("/api/search", params={"q": "DOPO"}).json()["results"]
    assert any(item["id"] == "chem-dopo" for item in results)
    record = client.get("/api/chemicals/chem-boric").json()
    assert record["properties"]
    assert {prop["origin"] for prop in record["properties"]} == {"measured", "ai_estimated"}
    assert all("source_title" in prop for prop in record["properties"])


def test_structure_proxy_generates_real_3d_coordinates_locally(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b"Paclitaxel\n  Cactus 3D\n  1  0  0  0\nM  END\n"

    seen = []

    def fake_urlopen(request, timeout):
        seen.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr(main_module, "urlopen", fake_urlopen)
    response = main_module._structure_asset("3d", fallback_smiles="CC(C)O", fallback_name="isopropanol")

    assert response.status_code == 200
    assert response.headers["x-chemrd-structure-origin"] == "calculated"
    assert "RDKit" in response.headers["x-chemrd-structure-source"]
    from rdkit import Chem
    mol = Chem.MolFromMolBlock(response.body.decode(), removeHs=False)
    assert mol.GetNumAtoms() == 12
    assert mol.GetConformer().Is3D()
    assert seen == []


def test_analysis_graph_and_connector_preview():
    analysis = client.get("/api/analyses/ana-tga-resole").json()
    assert analysis["technique"] == "TGA/DTG"
    assert len(analysis["series"]) == 2
    graph = client.get("/api/graph").json()
    assert len(graph["nodes"]) >= 7
    assert all("smiles" in node for node in graph["nodes"] if node["type"] == "chemical")
    response = client.post("/api/ingest/preview", json={"connector_id": "local-file", "payload": {"filename": "owned.csv", "records": 12}})
    assert response.status_code == 200
    assert response.json()["records_seen"] == 12


def test_chemical_property_plot_catalog_keeps_metric_origins_explicit():
    response = client.get("/api/plots/chemical-properties")
    assert response.status_code == 200
    body = response.json()
    assert any(metric["key"] == "identity:molecular_weight" for metric in body["metrics"])
    assert any(metric["origin"] == "measured" for metric in body["metrics"])
    boric = next(point for point in body["points"] if point["chemical_id"] == "chem-boric")
    assert boric["values"]["property:decomposition_onset:measured:°C"]["source_title"] == "ChemR&D pilot thermal run TGA-024"


def test_assistant_has_citations():
    response = client.post("/api/assistant", json={"question": "What does the thermal screen show?", "live": False})
    assert response.status_code == 200
    body = response.json()
    assert body["citations"]
    assert "measured" in body["answer"]


def test_live_research_endpoint_keeps_provider_contract(monkeypatch):
    import backend.app.main as main_module

    monkeypatch.setattr(main_module, "live_research", lambda query: {
        "query": query,
        "results": [{"type": "live_chemical", "name": "Paclitaxel", "source_url": "https://example.test/paclitaxel"}],
        "citations": [{"id": "chemical-identity-36314", "label": "Chemical identity record", "url": "https://example.test/paclitaxel", "reason": "chemical"}],
        "answer": "Live result",
        "providers": {"Chemical identity (Cactus/OPSIN)": {"status": "ok", "records": 1}},
        "retrieved_at": "2026-09-27T00:00:00+00:00",
    })
    response = client.get("/api/research/live", params={"q": "Paclitaxel"})
    assert response.status_code == 200
    assert response.json()["results"][0]["type"] == "live_chemical"
    assert response.json()["citations"][0]["url"].startswith("https://")


def test_chemical_identity_prefers_structured_chebi_record(monkeypatch):
    import backend.app.live_research as research

    def fake_json(url):
        if "es_search" in url:
            return {"results": [{"_source": {"id": "CHEBI:12345", "name": "capsaicin"}}]}, None
        return {
            "name": "capsaicin",
            "definition": "A vanilloid compound.",
            "chemical_data": {"formula": "C18H27NO3", "mass": 305.41},
            "default_structure": {
                "smiles": "COc1cc(CNC(=O)CCCC/C=C/C(C)C)ccc1O",
                "standard_inchi": "InChI=1S/demo",
                "standard_inchi_key": "DEMO-CAPSAICIN-KEY",
            },
            "names": {"SYNONYMS": [{"name": "capsaicin"}, {"name": "CAS 404-86-4"}]},
        }, None

    monkeypatch.setattr(research, "_fetch_json", fake_json)
    monkeypatch.setattr(research, "_fetch_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Cactus must not be queried when ChEBI has a structure")))
    monkeypatch.setattr(research, "_rdkit_descriptors", lambda _smiles: {"formula": "C18H27NO3", "molecular_weight": 305.41, "exact_mass": 305.20})

    record, error = research.chemical_identity_lookup("Capsaicin")

    assert error is None
    assert record["source"]["publisher"] == "EMBL-EBI ChEBI"
    assert record["source_url"].startswith("https://www.ebi.ac.uk/chebi/searchId.do")
    assert "cactus.nci.nih.gov" not in record["source_url"]
    assert record["identifiers"]["chebi_id"] == "CHEBI:12345"
    assert record["properties"]


def test_live_assistant_uses_ai_synthesis_only_in_assistant_route(monkeypatch):
    import backend.app.main as main_module

    monkeypatch.setattr(main_module, "live_research", lambda query: {
        "query": query,
        "results": [{
            "type": "live_chemical",
            "name": "Paclitaxel",
            "formula": "C47H51NO14",
            "molecular_weight": 853.9,
            "iupac_name": "demo IUPAC name",
            "inchikey": "demo-key",
            "source_url": "https://example.test/paclitaxel",
            "source": {"id": "chemical-identity-demo", "title": "Chemical identity demo", "license": "public"},
        }],
        "citations": [{"id": "chemical-identity-demo", "label": "Chemical identity demo", "url": "https://example.test/paclitaxel", "reason": "chemical"}],
        "answer": "retrieval fallback",
        "providers": {"Chemical identity (Cactus/OPSIN)": {"status": "ok", "records": 1}},
        "retrieved_at": "2026-09-27T00:00:00+00:00",
    })
    monkeypatch.setattr(main_module, "synthesize", lambda question, evidence: ("AI answer [S1]", None))
    response = client.post("/api/assistant", json={"question": "What is paclitaxel?", "live": True})
    assert response.status_code == 200
    body = response.json()
    assert body["assistant_mode"] == "llm_synthesis"
    assert body["assistant_status"] == "AI synthesis active"
    assert body["answer"] == "AI answer [S1]"


def test_llm_falls_back_between_configured_providers(monkeypatch):
    import backend.app.llm as llm

    monkeypatch.setenv("LLM_PROVIDER_ORDER", "openai,groq")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")

    def fake_provider(provider, instructions, prompt):
        if provider == "openai":
            raise RuntimeError("rate limited")
        return "Groq answer [S1]"

    monkeypatch.setattr(llm, "_synthesize_openai", fake_provider)
    answer, error, provider = llm.synthesize(
        "What is paclitaxel?",
        {"results": [{"type": "live_chemical", "name": "Paclitaxel", "formula": "C47H51NO14"}]},
    )
    assert answer == "Groq answer [S1]"
    assert error is None
    assert provider == "groq"


def test_hindsight_memory_scopes_recall_and_queues_turn(monkeypatch):
    import backend.app.hindsight_memory as memory_module

    monkeypatch.setenv("HINDSIGHT_ENABLED", "true")
    monkeypatch.setenv("HINDSIGHT_API_URL", "https://hindsight.example.test")
    monkeypatch.setenv("HINDSIGHT_API_KEY", "hsk-test")
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        calls.append({"url": request.full_url, "body": json.loads(request.data.decode("utf-8")), "auth": request.headers.get("Authorization")})
        if request.full_url.endswith("/banks/chemrd-user-123"):
            return FakeResponse({"bank_id": "chemrd-user-123"})
        if request.full_url.endswith("/memories/recall"):
            return FakeResponse({"results": [{"id": "m-1", "text": "The user is evaluating DOPO in resole systems.", "type": "experience"}]})
        return FakeResponse({"success": True, "async": True})

    monkeypatch.setattr(memory_module, "urlopen", fake_urlopen)
    adapter = memory_module.HindsightMemory()
    recalled = adapter.recall("user-123", "What am I evaluating?")
    retained = adapter.retain_turn("user-123", "What am I evaluating?", "You are evaluating DOPO.", 2)

    assert recalled["status"] == "ok"
    assert recalled["items"][0]["text"].startswith("The user")
    assert retained == "queued"
    assert calls[0]["auth"] == "Bearer hsk-test"
    assert "tags" not in calls[1]["body"]
    assert "tags_match" not in calls[1]["body"]
    assert calls[3]["body"]["items"][0]["document_id"].startswith("assistant-turn-")
    assert calls[3]["body"]["items"][0]["timestamp"].endswith("+00:00")
    assert calls[3]["body"]["async"] is False


def test_chemical_name_parser_handles_greek_prefix_and_conversation():
    from backend.app.live_research import _entity_candidate, _is_generic_follow_up, _identity_intent

    assert _entity_candidate("β-Cyclodextrin tell me about it") == "beta-Cyclodextrin"
    assert _identity_intent("tell me about paclitaxel")
    assert _is_generic_follow_up("Tell me about it")


def test_chemical_identity_lookup_uses_cactus_only_as_fallback(monkeypatch):
    import backend.app.live_research as live

    def fake_fetch_text(url, accept="text/plain"):
        if url.endswith("/smiles"):
            return "CCO\n", None
        if url.endswith("/stdinchi"):
            return "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3\n", None
        if url.endswith("/stdinchikey"):
            return "LFQSCWFLJHTTHZ-UHFFFAOYSA-N\n", None
        return "ethanol\nethyl alcohol\n64-17-5\n", None

    monkeypatch.setattr(live, "_fetch_text", fake_fetch_text)
    monkeypatch.setattr(live, "_fetch_json", lambda *_: (None, 'offline'))
    monkeypatch.setattr(live, "_rdkit_descriptors", lambda smiles: {"formula": "C2H6O", "molecular_weight": 46.07, "tpsa": 20.23})
    record, error = live.chemical_identity_lookup("ethanol")
    assert error is None
    assert record["id"].startswith("chemical-")
    assert record["smiles"] == "CCO"
    assert record["cas_numbers"] == ["64-17-5"]
    assert record["source"]["publisher"] == "NCI/CADD Cactus (fallback)"
    assert record["source"]["metadata"]["descriptor_origin"] == "calculated locally with RDKit"
    assert record["source"]["publisher"] == "NCI/CADD Cactus (fallback)"
    assert record["source_url"].startswith("https://cactus.nci.nih.gov/")


def test_chemical_identity_lookup_can_fall_back_to_chebi(monkeypatch):
    import backend.app.live_research as live

    monkeypatch.setattr(live, "_fetch_text", lambda url, accept="text/plain": (None, "offline"))

    def fake_fetch_json(url):
        if "/es_search/" in url:
            return {"results": [{"_source": {"chebi_accession": "CHEBI:15365", "name": "aspirin"}}]}, None
        return {
            "name": "aspirin",
            "chemical_data": {"formula": "C9H8O4", "mass": 180.16},
            "default_structure": {
                "smiles": "CC(=O)Oc1ccccc1C(=O)O",
                "standard_inchi": "InChI=1S/C9H8O4/c1-6(10)13-8-5-3-2-4-7(8)9(11)12/h2-5H,1H3,(H,11,12)",
                "standard_inchi_key": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
            },
            "names": {},
        }, None

    monkeypatch.setattr(live, "_fetch_json", fake_fetch_json)
    monkeypatch.setattr(live, "_rdkit_descriptors", lambda smiles: {"formula": "C9H8O4", "molecular_weight": 180.16})
    record, error = live.chemical_identity_lookup("aspirin")
    assert error is None
    assert record["source"]["publisher"] == "EMBL-EBI ChEBI"
    assert record["source"]["metadata"]["chebi_id"] == "CHEBI:15365"
    assert record["source_url"] == "https://www.ebi.ac.uk/chebi/searchId.do?chebiId=CHEBI:15365"
    assert "/backend/api/" not in record["source_url"]
    assert record["inchikey"] == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"


def test_live_assistant_does_not_replace_ambiguous_query_with_seeded_demo(monkeypatch):
    import backend.app.main as main_module

    monkeypatch.setattr(main_module, "live_research", lambda query: {
        "query": query,
        "results": [],
        "citations": [],
        "answer": "Please include the chemical or topic.",
        "providers": {},
    })
    response = client.post("/api/assistant", json={"question": "Tell me about it", "live": True})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Please include the chemical or topic."
    assert body["assistant_status"] == "Needs a specific subject"


def test_live_assistant_answers_general_questions_without_unrelated_citations(monkeypatch):
    import backend.app.main as main_module

    monkeypatch.setattr(main_module, "live_research", lambda query: {
        "query": query,
        "results": [{
            "type": "live_paper",
            "name": "Neural Organization of Episodic Memory and Navigation",
            "abstract": "A study of memory and navigation in adults.",
            "source_url": "https://example.test/unrelated",
            "source": {"id": "unrelated", "title": "Unrelated paper", "license": "public"},
        }],
        "citations": [{"id": "unrelated", "label": "Unrelated paper", "url": "https://example.test/unrelated", "reason": "paper"}],
        "answer": "retrieval fallback",
        "providers": {"OpenAlex": {"status": "ok", "records": 1}},
        "retrieved_at": "2026-09-28T00:00:00+00:00",
    })

    def fake_synthesize(question, evidence):
        assert evidence["results"] == []
        assert evidence["citations"] == []
        return "Direct answer:\nAcid-base conditions can change chemical stability by changing protonation and reaction rates.", None, "groq"

    monkeypatch.setattr(main_module, "synthesize", fake_synthesize)
    response = client.post("/api/assistant", json={"question": "How does pH affect chemical stability?", "live": True})
    assert response.status_code == 200
    body = response.json()
    assert body["assistant_mode"] == "llm_synthesis"
    assert body["assistant_provider"] == "groq"
    assert body["citations"] == []
    assert body["results"] == []
    assert "protonation" in body["answer"]


def test_library_folders_accept_chemicals_and_research_items():
    folders = client.get("/api/library/folders").json()
    assert folders
    folder_id = folders[0]["id"]
    chemical = client.post("/api/library/items", json={
        "folder_id": folder_id,
        "item_type": "chemical",
        "title": "Boric acid",
        "chemical_id": "chem-boric",
    })
    assert chemical.status_code == 200
    website = client.post("/api/library/items", json={
        "folder_id": folder_id,
        "item_type": "website",
        "title": "Useful research page",
        "url": "https://example.test/research",
        "content": "Follow-up reading",
    })
    assert website.status_code == 200
    items = client.get(f"/api/library/folders/{folder_id}/items").json()
    assert {item["item_type"] for item in items} >= {"chemical", "website"}


def test_save_live_chemical_creates_library_record_with_provenance():
    folders = client.get("/api/library/folders").json()
    folder_id = folders[0]["id"]
    response = client.post("/api/library/chemicals", json={
        "folder_id": folder_id,
        "record": {
            "id": "chemical-test-live-123",
            "name": "Test live compound",
            "formula": "C2H6O",
            "molecular_weight": 46.07,
            "smiles": "CCO",
            "source_url": "https://example.test/compound/123",
            "source": {
                "id": "source-test-live-123",
                "title": "Test public chemical source",
                "source_type": "public_database",
                "publisher": "Test publisher",
                "url": "https://example.test/compound/123",
                "license": "Test license",
                "metadata": {"cid": 123},
            },
        },
    })
    assert response.status_code == 200
    item = response.json()
    assert item["item_type"] == "chemical"
    assert item["chemical_name"] == "Test live compound"
    assert item["source_title"] == "Test public chemical source"
    chemical = client.get(f"/api/chemicals/{item['chemical_id']}")
    assert chemical.status_code == 200
    record = chemical.json()
    assert record["structure_2d_url"] == f"/api/chemicals/{item['chemical_id']}/structure-2d"
    assert record["conformer_3d_url"] == f"/api/chemicals/{item['chemical_id']}/conformer-3d"


def test_save_live_chemical_persists_provider_properties_with_origin():
    folder_id = client.get("/api/library/folders").json()[0]["id"]
    response = client.post("/api/library/chemicals", json={
        "folder_id": folder_id,
        "record": {
            "id": "chemical-test-live-properties",
            "name": "Test property compound",
            "formula": "C3H8O",
            "molecular_weight": 60.1,
            "smiles": "CCCO",
            "xlogp": 0.25,
            "tpsa": 20.2,
            "h_bond_donors": 1,
            "iupac_name": "propan-1-ol",
            "source": {"id": "source-test-live-properties", "title": "Cactus test source", "metadata": {"provider": "NCI/CADD Cactus"}},
        },
    })
    assert response.status_code == 200
    chemical_id = response.json()["chemical_id"]
    props = client.get(f"/api/chemicals/{chemical_id}").json()["properties"]
    by_name = {prop["property_name"]: prop for prop in props}
    assert by_name["xlogp"]["origin"] == "calculated"
    assert by_name["tpsa"]["source_title"] == "Cactus test source"
    assert by_name["iupac_name"]["origin"] == "literature_extracted"


def test_structure_asset_proxy_keeps_public_assets_same_origin(monkeypatch):
    import backend.app.main as main_module

    folder_id = client.get("/api/library/folders").json()[0]["id"]
    saved = client.post("/api/library/chemicals", json={
        "folder_id": folder_id,
        "record": {
            "id": "chemical-asset-proxy-test",
            "name": "Asset proxy test compound",
            "formula": "C2H6O",
            "molecular_weight": 46.07,
            "smiles": "CCO",
            "source": {"id": "source-asset-proxy-test", "title": "Cactus asset test", "metadata": {"provider": "NCI/CADD Cactus"}},
        },
    })
    assert saved.status_code == 200
    chemical_id = saved.json()["chemical_id"]

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b"fake-structure-asset"

    seen = []

    def fake_urlopen(request, timeout):
        seen.append((request.full_url, timeout))
        return FakeResponse()

    monkeypatch.setattr(main_module, "urlopen", fake_urlopen)
    image = client.get(f"/api/chemicals/{chemical_id}/structure-2d")
    sdf = client.get(f"/api/chemicals/{chemical_id}/conformer-3d")
    public_image = client.get("/api/live-structure/2d", params={"smiles": "CCO", "name": "ethanol"})
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/svg+xml")
    assert '<path' in image.text
    assert sdf.status_code == 200
    assert sdf.headers["content-type"].startswith("chemical/x-mdl-sdfile")
    assert public_image.status_code == 200
    assert seen == []
    from rdkit import Chem
    assert Chem.MolFromMolBlock(sdf.text).GetConformer().Is3D()
