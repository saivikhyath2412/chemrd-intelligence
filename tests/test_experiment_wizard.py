import pytest
from fastapi.testclient import TestClient
import backend.app.main as main_module

@pytest.fixture(autouse=True)
def ensure_db():
    main_module.store.initialize()
    yield

client = TestClient(main_module.app)



def test_create_experiment_endpoint():
    payload = {
        "name": "Boron-Resole Coordination Test",
        "objective": "Verify borate chelation with resole ortho-hydroxyls",
        "experiment_type": "Compatibility Test",
        "owner": "Dr. Aris",
        "date": "2026-09-28",
        "chemical_ids": ["chem-phenol", "chem-boric"],
        "status": "planned"
    }
    response = client.post("/api/experiments", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == payload["name"]
    assert data["objective"] == payload["objective"]
    assert "chem-phenol" in data["chemical_ids"]
    assert data["status"] == "planned"

def test_simulate_experiment_endpoint_with_catalyst():
    payload = {
        "name": "Base-Catalyzed Phenolic Resole Synthesis",
        "objective": "Synthesize resole oligomer using NaOH catalyst",
        "experiment_type": "Synthesis",
        "chemicals": [
            {
                "id": "chem-phenol",
                "name": "Phenol",
                "formula": "C6H6O",
                "smiles": "C1=CC=C(C=C1)O",
                "role": "Reactant",
                "quantity": 94.1,
                "quantity_unit": "g",
                "temp_min": 20.0,
                "temp_max": 90.0,
                "concentration": 100.0,
                "concentration_unit": "w/w"
            },
            {
                "id": "chem-formaldehyde",
                "name": "Formaldehyde",
                "formula": "CH2O",
                "smiles": "C=O",
                "role": "Reactant",
                "quantity": 37.0,
                "quantity_unit": "g"
            },
            {
                "id": "chem-naoh",
                "name": "Sodium Hydroxide (NaOH)",
                "formula": "NaOH",
                "role": "Catalyst",
                "quantity": 4.0,
                "quantity_unit": "g",
                "cat_mechanism": "Base-Catalyzed Phenoxide Generation",
                "cat_ea_reduction": 35.0,
                "cat_selectivity": "Regioselective (ortho/para preference)"
            }
        ],
        "global_conditions": {
            "reaction_temp": 75.0,
            "duration": 2.5,
            "duration_unit": "hours",
            "atmosphere": "Nitrogen (N₂)",
            "stirring_speed": 450.0
        }
    }
    response = client.post("/api/experiments/simulate", json=payload)
    assert response.status_code == 200
    res = response.json()
    assert "balanced_equation" in res
    assert "feasibility_score" in res
    assert res["catalyst_influence"] is not None
    assert res["catalyst_influence"]["present"] is True
    assert "rate_enhancement" in res["catalyst_influence"]
    assert "color_change" in res
    assert "temperature_change" in res
    assert len(res["temperature_change"]["time_points"]) > 0
    assert "products" in res
    assert len(res["products"]) > 0
    assert "main_product_details" in res
    assert "safety_warnings" in res
