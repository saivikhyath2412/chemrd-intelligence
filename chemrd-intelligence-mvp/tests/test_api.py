import os
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test-chemrd.db"
os.environ["CHEMRD_SEED"] = "true"

from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.main import app, store


client = TestClient(app)


def teardown_module():
    if Path("test-chemrd.db").exists():
        Path("test-chemrd.db").unlink()


def test_health_and_dashboard():
    assert client.get("/api/health").json()["status"] == "ok"
    payload = client.get("/api/dashboard").json()
    assert payload["counts"]["chemicals"] >= 5
    assert {item["origin"] for item in payload["origin_counts"]} >= {"measured", "calculated", "ai_estimated"}


def test_search_and_chemical_provenance():
    results = client.get("/api/search", params={"q": "DOPO"}).json()["results"]
    assert any(item["id"] == "chem-dopo" for item in results)
    record = client.get("/api/chemicals/chem-boric").json()
    assert record["properties"]
    assert {prop["origin"] for prop in record["properties"]} == {"measured", "ai_estimated"}
    assert all("source_title" in prop for prop in record["properties"])


def test_paclitaxel_uses_nih3d_fallback(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b"Paclitaxel\n  NIH 3D\n  1  0  0  0\nM  END\n"

    seen = []

    def fake_urlopen(request, timeout):
        seen.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr(main_module, "urlopen", fake_urlopen)
    response = main_module._pubchem_asset_by_cid("36314", "3d", fallback_name="Paclitaxel")

    assert response.status_code == 200
    assert response.headers["x-chemrd-structure-origin"] == "model_predicted"
    assert "NIH 3D" in response.headers["x-chemrd-structure-source"]
    assert seen == ["https://3d.nih.gov/api/files/94543"]


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
        "citations": [{"id": "pubchem-36314", "label": "PubChem compound record", "url": "https://example.test/paclitaxel", "reason": "chemical"}],
        "answer": "Live result",
        "providers": {"PubChem": {"status": "ok", "records": 1}},
        "retrieved_at": "2026-09-27T00:00:00+00:00",
    })
    response = client.get("/api/research/live", params={"q": "Paclitaxel"})
    assert response.status_code == 200
    assert response.json()["results"][0]["type"] == "live_chemical"
    assert response.json()["citations"][0]["url"].startswith("https://")


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
            "source": {"id": "pubchem-demo", "title": "PubChem demo", "license": "public"},
        }],
        "citations": [{"id": "pubchem-demo", "label": "PubChem demo", "url": "https://example.test/paclitaxel", "reason": "chemical"}],
        "answer": "retrieval fallback",
        "providers": {"PubChem": {"status": "ok", "records": 1}},
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


def test_chemical_name_parser_handles_greek_prefix_and_conversation():
    from backend.app.live_research import _entity_candidate, _is_generic_follow_up, _identity_intent

    assert _entity_candidate("β-Cyclodextrin tell me about it") == "beta-Cyclodextrin"
    assert _identity_intent("tell me about paclitaxel")
    assert _is_generic_follow_up("Tell me about it")


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
            "id": "pubchem-test-live-123",
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


def test_save_live_chemical_persists_pubchem_properties_with_origin():
    folder_id = client.get("/api/library/folders").json()[0]["id"]
    response = client.post("/api/library/chemicals", json={
        "folder_id": folder_id,
        "record": {
            "id": "pubchem-test-live-properties",
            "name": "Test property compound",
            "formula": "C3H8O",
            "molecular_weight": 60.1,
            "smiles": "CCCO",
            "xlogp": 0.25,
            "tpsa": 20.2,
            "h_bond_donors": 1,
            "iupac_name": "propan-1-ol",
            "source": {"id": "source-test-live-properties", "title": "PubChem test source", "metadata": {"cid": 987654}},
        },
    })
    assert response.status_code == 200
    chemical_id = response.json()["chemical_id"]
    props = client.get(f"/api/chemicals/{chemical_id}").json()["properties"]
    by_name = {prop["property_name"]: prop for prop in props}
    assert by_name["xlogp"]["origin"] == "calculated"
    assert by_name["tpsa"]["source_title"] == "PubChem test source"
    assert by_name["iupac_name"]["origin"] == "literature_extracted"


def test_structure_asset_proxy_keeps_public_assets_same_origin(monkeypatch):
    import backend.app.main as main_module

    folder_id = client.get("/api/library/folders").json()[0]["id"]
    saved = client.post("/api/library/chemicals", json={
        "folder_id": folder_id,
        "record": {
            "id": "pubchem-asset-proxy-test",
            "name": "Asset proxy test compound",
            "formula": "C2H6O",
            "molecular_weight": 46.07,
            "smiles": "CCO",
            "source": {"id": "source-asset-proxy-test", "title": "PubChem asset test", "metadata": {"cid": 12345}},
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
    public_image = client.get("/api/pubchem/12345/structure-2d")
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")
    assert sdf.status_code == 200
    assert sdf.headers["content-type"].startswith("chemical/x-mdl-sdfile")
    assert public_image.status_code == 200
    assert len(seen) == 3
    assert "/PNG?image_size=large" in seen[0][0]
    assert "/SDF?record_type=3d" in seen[1][0]
