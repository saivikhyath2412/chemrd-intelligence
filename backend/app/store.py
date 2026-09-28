from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .chemistry import structure_svg


ORIGINS = {"measured", "literature_extracted", "calculated", "model_predicted", "ai_estimated"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, source_type TEXT NOT NULL,
 publisher TEXT, url TEXT, license TEXT NOT NULL, accessed_at TEXT, metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS chemicals (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, synonyms TEXT NOT NULL DEFAULT '[]', cas_number TEXT,
 smiles TEXT, inchi TEXT, inchikey TEXT, formula TEXT, molecular_weight REAL, description TEXT, structure_svg TEXT
);
CREATE TABLE IF NOT EXISTS property_values (
 id TEXT PRIMARY KEY, chemical_id TEXT NOT NULL, property_name TEXT NOT NULL, value_text TEXT NOT NULL,
 numeric_value REAL, unit TEXT, origin TEXT NOT NULL, source_id TEXT, method TEXT, confidence REAL, notes TEXT,
 FOREIGN KEY(chemical_id) REFERENCES chemicals(id), FOREIGN KEY(source_id) REFERENCES sources(id)
);
CREATE TABLE IF NOT EXISTS experiments (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL, objective TEXT, chemical_ids TEXT NOT NULL DEFAULT '[]',
 owner TEXT, started_at TEXT, updated_at TEXT, source_id TEXT, FOREIGN KEY(source_id) REFERENCES sources(id)
);
CREATE TABLE IF NOT EXISTS formulations (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, recipe TEXT NOT NULL, purpose TEXT, status TEXT NOT NULL, source_id TEXT,
 FOREIGN KEY(source_id) REFERENCES sources(id)
);
CREATE TABLE IF NOT EXISTS reactions (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, reaction_type TEXT, inputs TEXT NOT NULL DEFAULT '[]', outputs TEXT NOT NULL DEFAULT '[]',
 conditions TEXT NOT NULL DEFAULT '{}', yield_text TEXT, source_id TEXT, FOREIGN KEY(source_id) REFERENCES sources(id)
);
CREATE TABLE IF NOT EXISTS analyses (
 id TEXT PRIMARY KEY, experiment_id TEXT, technique TEXT NOT NULL, title TEXT NOT NULL, x_unit TEXT, y_unit TEXT,
 series TEXT NOT NULL, origin TEXT NOT NULL, source_id TEXT, FOREIGN KEY(experiment_id) REFERENCES experiments(id), FOREIGN KEY(source_id) REFERENCES sources(id)
);
CREATE TABLE IF NOT EXISTS library_folders (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS library_items (
 id TEXT PRIMARY KEY, folder_id TEXT NOT NULL, item_type TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
 content TEXT NOT NULL DEFAULT '', chemical_id TEXT, source_id TEXT, metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(folder_id) REFERENCES library_folders(id) ON DELETE CASCADE,
 FOREIGN KEY(chemical_id) REFERENCES chemicals(id), FOREIGN KEY(source_id) REFERENCES sources(id)
);
CREATE INDEX IF NOT EXISTS library_items_folder_idx ON library_items(folder_id);
CREATE INDEX IF NOT EXISTS library_items_chemical_idx ON library_items(chemical_id);
"""


class Store:
    def __init__(self, database_url: str | None = None):
        database_url = database_url or os.getenv("DATABASE_URL", "sqlite:///./chemrd.db")
        if not database_url.startswith("sqlite"):
            # The adapter keeps the production target explicit while keeping the
            # zero-setup MVP reliable in environments without a DB driver.
            raise RuntimeError("This MVP uses SQLite locally. Install the postgres extras and replace Store for DATABASE_URL=%s" % database_url)
        path = database_url.removeprefix("sqlite:///")
        self.path = Path(path)
        if not self.path.is_absolute():
            self.path = Path.cwd() / self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        with closing(self.connect()) as conn:
            conn.executescript(SCHEMA)
            try:
                conn.execute("ALTER TABLE experiments ADD COLUMN experiment_type TEXT DEFAULT 'Custom'")
            except sqlite3.OperationalError:
                pass
            timestamp = utc_now()
            conn.executemany(
                "INSERT OR IGNORE INTO library_folders (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
                [
                    ("folder-unsorted", "Unsorted", "Temporary holding folder for items you have not organized yet.", timestamp, timestamp),
                ],
            )
            conn.commit()
        self.clear_seed_data()


    def clear_seed_data(self) -> None:
        """Remove only the app's known demo rows when clean mode is enabled."""
        if os.getenv("CHEMRD_CLEAR_SEED_DATA", "false").lower() in {"0", "false", "no"}:
            return
        demo_analyses = ("ana-tga-resole", "ana-dsc-resole", "ana-ftir-boric", "ana-xrd-dopo")
        demo_reactions = ("rxn-resole", "rxn-dopo-blend")
        demo_formulations = ("form-fp-01", "form-resole-base")
        demo_experiments = ("exp-tga-024", "exp-dsc-011", "exp-ftir-007")
        demo_chemicals = ("chem-phenol", "chem-resole", "chem-dopo", "chem-boric", "chem-formaldehyde")
        demo_sources = ("src-pubchem-demo", "src-journal-phenolic", "src-lab-thermal", "src-patent-dopo")
        with closing(self.connect()) as conn:
            for table, ids in (("analyses", demo_analyses), ("reactions", demo_reactions), ("formulations", demo_formulations), ("experiments", demo_experiments)):
                placeholders = ",".join("?" for _ in ids)
                conn.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)
            for chemical_id in demo_chemicals:
                referenced = conn.execute("SELECT 1 FROM library_items WHERE chemical_id = ? LIMIT 1", (chemical_id,)).fetchone()
                if referenced:
                    continue
                conn.execute("DELETE FROM property_values WHERE chemical_id = ?", (chemical_id,))
                conn.execute("DELETE FROM chemicals WHERE id = ?", (chemical_id,))
            for source_id in demo_sources:
                referenced = conn.execute("""
                    SELECT 1 FROM property_values WHERE source_id = ?
                    UNION SELECT 1 FROM experiments WHERE source_id = ?
                    UNION SELECT 1 FROM formulations WHERE source_id = ?
                    UNION SELECT 1 FROM reactions WHERE source_id = ?
                    UNION SELECT 1 FROM analyses WHERE source_id = ?
                    UNION SELECT 1 FROM library_items WHERE source_id = ?
                    LIMIT 1
                """, (source_id, source_id, source_id, source_id, source_id, source_id)).fetchone()
                if not referenced:
                    conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
            for folder_id in ("folder-saved", "folder-phenolic", "folder-literature"):
                conn.execute("DELETE FROM library_folders WHERE id = ? AND NOT EXISTS (SELECT 1 FROM library_items WHERE folder_id = ?)", (folder_id, folder_id))
            timestamp = utc_now()
            conn.execute(
                "INSERT OR IGNORE INTO library_folders (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
                ("folder-unsorted", "Unsorted", "Temporary holding folder for items you have not organized yet.", timestamp, timestamp),
            )
            conn.commit()

    def seed(self) -> None:
        with closing(self.connect()) as conn:
            if conn.execute("SELECT COUNT(*) FROM chemicals").fetchone()[0]:
                return
            sources = [
                ("src-pubchem-demo", "PubChem-style identity reference", "public_database", "NCBI", "https://pubchem.ncbi.nlm.nih.gov/", "public-domain facts / verify terms", utc_now(), "{}"),
                ("src-journal-phenolic", "Phenolic resins for high-temperature composites", "paper", "Materials Chemistry Review", "https://doi.org/10.0000/demo-phenolic", "licensed research reference", utc_now(), '{"doi":"10.0000/demo-phenolic"}'),
                ("src-lab-thermal", "ChemR&D pilot thermal run TGA-024", "internal_experiment", "ChemR&D pilot lab", "", "company-internal", utc_now(), '{"instrument":"TGA/DSC-01"}'),
                ("src-patent-dopo", "Phosphorus flame-retardant epoxy compositions", "patent", "Example Patent Office", "https://patents.example/EP-DEMO-2042", "licensed patent record", utc_now(), '{"patent":"EP-DEMO-2042"}'),
            ]
            conn.executemany("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", sources)
            chemicals = [
                ("chem-phenol", "Phenol", '["hydroxybenzene"]', "108-95-2", "C1=CC=C(C=C1)O", "InChI=1S/C6H6O/c7-6-5-3-1-2-4-6/h1-4,7H", "ISWSIDIOOBJBQZ-UHFFFAOYSA-N", "C6H6O", 94.11, "Aromatic phenol used as a reference monomer and resin building block."),
                ("chem-resole", "Phenolic resole resin", '["resole","phenol-formaldehyde resin"]', None, "Oc1ccc(cc1)CO", None, None, "C7H8O2", 124.14, "Demo oligomeric resole motif for formulation and thermal screening."),
                ("chem-dopo", "DOPO", '["9,10-dihydro-9-oxa-10-phosphaphenanthrene-10-oxide"]', "35948-25-5", "O=P1(OP2=CC=CC=C2C3=CC=CC=C31)C", None, None, "C12H9O2P", 216.17, "Phosphorus-containing flame-retardant additive."),
                ("chem-boric", "Boric acid", '["orthoboric acid","hydrogen borate"]', "10043-35-3", "B(O)(O)O", "InChI=1S/BH3O3/c2-1(3)4/h(H3,2,3,4)", "KGBXLFKZBHKPEV-UHFFFAOYSA-N", "H3BO3", 61.83, "Boron source used as a char-promoting and inorganic additive."),
                ("chem-formaldehyde", "Formaldehyde", '["methanal"]', "50-00-0", "C=O", None, None, "CH2O", 30.03, "Demo crosslinking reagent used in resole process records."),
            ]
            for row in chemicals:
                conn.execute("INSERT INTO chemicals VALUES (?,?,?,?,?,?,?,?,?,?,?)", (*row, structure_svg(row[4])))
            properties = [
                ("pv-phenol-mp", "chem-phenol", "melting_point", "40.5", 40.5, "°C", "measured", "src-pubchem-demo", "curated reference", 0.98, "Reference value; not a resin specification."),
                ("pv-phenol-logp", "chem-phenol", "logP", "1.46", 1.46, None, "calculated", None, "RDKit Crippen when available", 0.82, "Calculation provenance kept separate from measured values."),
                ("pv-resole-tg", "chem-resole", "glass_transition", "92", 92, "°C", "literature_extracted", "src-journal-phenolic", "table 2 extraction", 0.79, "Demo extracted range center."),
                ("pv-resole-char", "chem-resole", "char_yield_800c", "48", 48, "%", "measured", "src-lab-thermal", "TGA-024", 0.97, "Nitrogen atmosphere."),
                ("pv-dopo-p", "chem-dopo", "phosphorus_content", "14.3", 14.3, "%", "calculated", None, "formula calculation", 0.99, None),
                ("pv-dopo-flame", "chem-dopo", "flame_retardancy_index", "0.73", 0.73, None, "model_predicted", "src-patent-dopo", "demo QSAR model", 0.64, "Use as a screening signal only."),
                ("pv-boric-decomp", "chem-boric", "decomposition_onset", "170", 170, "°C", "measured", "src-lab-thermal", "TGA-024", 0.95, "Moisture-conditioned sample."),
                ("pv-boric-ai", "chem-boric", "compatibility_score", "0.68", 0.68, None, "ai_estimated", "src-journal-phenolic", "assistant estimate", 0.41, "Requires experimental confirmation."),
            ]
            conn.executemany("INSERT INTO property_values VALUES (?,?,?,?,?,?,?,?,?,?,?)", properties)
            experiments = [
                ("exp-tga-024", "Resole + DOPO thermal screen", "in_progress", "Compare char yield and decomposition onset across additive loading.", '["chem-resole","chem-dopo","chem-boric"]', "A. Rao", "2026-09-20", "2026-09-27", "src-lab-thermal"),
                ("exp-dsc-011", "Phenolic cure window", "completed", "Locate cure exotherm and post-cure Tg for three resin lots.", '["chem-phenol","chem-resole"]', "M. Iyer", "2026-08-04", "2026-08-20", "src-journal-phenolic"),
                ("exp-ftir-007", "Boron-resole compatibility", "planned", "Confirm borate coordination signatures by FTIR.", '["chem-resole","chem-boric"]', "S. Nair", "2026-10-02", "2026-10-02", "src-lab-thermal"),
            ]
            conn.executemany("INSERT INTO experiments (id, name, status, objective, chemical_ids, owner, started_at, updated_at, source_id) VALUES (?,?,?,?,?,?,?,?,?)", experiments)
            formulations = [
                ("form-fp-01", "FR-Resole Pilot 01", '{"chem-resole":72,"chem-dopo":18,"chem-boric":10}', "Halogen-free flame-retardant resin", "screening", "src-lab-thermal"),
                ("form-resole-base", "Resole Base R-01", '{"chem-phenol":82,"chem-formaldehyde":18}', "Reference phenolic resin", "reference", "src-journal-phenolic"),
            ]
            conn.executemany("INSERT INTO formulations VALUES (?,?,?,?,?,?)", formulations)
            reactions = [
                ("rxn-resole", "Phenol-formaldehyde resole synthesis", "condensation", '["chem-phenol","chem-formaldehyde"]', '["chem-resole"]', '{"catalyst":"NaOH","temperature":"75 °C","solvent":"water"}', "82% solids", "src-journal-phenolic"),
                ("rxn-dopo-blend", "DOPO additive incorporation", "physical_blend", '["chem-resole","chem-dopo"]', '["form-fp-01"]', '{"temperature":"90 °C","mix_time":"45 min"}', "homogeneous", "src-patent-dopo"),
            ]
            conn.executemany("INSERT INTO reactions VALUES (?,?,?,?,?,?,?,?)", reactions)
            analyses = [
                ("ana-tga-resole", "exp-tga-024", "TGA/DTG", "Resole + DOPO + boric acid thermal profile", "°C", "% mass", json.dumps({"series":[{"name":"FR-Resole 01","origin":"measured","points":[[50,100],[150,98],[250,96],[350,88],[450,68],[550,55],[650,49],[750,47],[850,46]]},{"name":"Resole control","origin":"measured","points":[[50,100],[150,98],[250,94],[350,79],[450,58],[550,39],[650,31],[750,29],[850,28]]}]}), "measured", "src-lab-thermal"),
                ("ana-dsc-resole", "exp-dsc-011", "DSC", "Phenolic cure window", "°C", "mW", json.dumps({"series":[{"name":"Lot A","origin":"measured","points":[[40,0.2],[80,0.4],[120,1.8],[160,3.7],[200,1.2],[240,0.3]]},{"name":"Lot B","origin":"measured","points":[[40,0.1],[80,0.3],[120,1.4],[160,3.1],[200,1.0],[240,0.2]]}]}), "measured", "src-journal-phenolic"),
                ("ana-ftir-boric", "exp-ftir-007", "FTIR", "Boron-resole compatibility scan", "cm⁻¹", "absorbance", json.dumps({"series":[{"name":"FR-Resole 01","origin":"measured","points":[[400,0.10],[700,0.22],[1000,0.18],[1250,0.45],[1500,0.30],[1750,0.16],[3000,0.28],[3400,0.52]]}]}), "measured", "src-lab-thermal"),
                ("ana-xrd-dopo", "exp-tga-024", "XRD", "Additive crystallinity check", "2θ", "intensity", json.dumps({"series":[{"name":"DOPO phase","origin":"literature_extracted","points":[[10,4],[15,8],[20,20],[25,7],[30,14],[35,5],[40,3]]}]}), "literature_extracted", "src-patent-dopo"),
            ]
            conn.executemany("INSERT INTO analyses VALUES (?,?,?,?,?,?,?,?,?)", analyses)
            conn.commit()

    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def sources(self) -> list[dict]:
        rows = self._rows("SELECT * FROM sources ORDER BY accessed_at DESC")
        for row in rows:
            row["metadata"] = json.loads(row["metadata"] or "{}")
        return rows

    def chemicals(self) -> list[dict]:
        rows = self._rows("SELECT * FROM chemicals ORDER BY name")
        for row in rows:
            row["synonyms"] = json.loads(row["synonyms"] or "[]")
        return rows

    def chemical(self, chemical_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM chemicals WHERE id = ?", (chemical_id,))
        if not rows:
            return None
        row = rows[0]
        row["synonyms"] = json.loads(row["synonyms"] or "[]")
        row["properties"] = self._rows("""SELECT pv.*, s.title AS source_title, s.license, s.url FROM property_values pv LEFT JOIN sources s ON s.id = pv.source_id WHERE chemical_id = ? ORDER BY property_name""", (chemical_id,))
        live = self._rows("SELECT metadata, source_id FROM library_items WHERE chemical_id = ? ORDER BY updated_at DESC LIMIT 1", (chemical_id,))
        if live:
            metadata = json.loads(live[0]["metadata"] or "{}")
            source_meta = (metadata.get("source") or {}).get("metadata") or {}
            cid = source_meta.get("cid")
            if cid:
                row["pubchem_cid"] = str(cid)
                row["structure_2d_url"] = f"/api/chemicals/{chemical_id}/structure-2d"
                row["conformer_3d_url"] = f"/api/chemicals/{chemical_id}/conformer-3d"
                row["live_source_url"] = f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}"
        return row

    def library_folders(self) -> list[dict]:
        return self._rows("""
            SELECT f.*, COUNT(i.id) AS item_count
            FROM library_folders f LEFT JOIN library_items i ON i.folder_id = f.id
            GROUP BY f.id ORDER BY f.name
        """)

    def create_library_folder(self, name: str, description: str = "") -> dict:
        folder_id = "folder-" + uuid.uuid4().hex[:12]
        timestamp = utc_now()
        with closing(self.connect()) as conn:
            try:
                conn.execute(
                    "INSERT INTO library_folders (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
                    (folder_id, name.strip(), description.strip(), timestamp, timestamp),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError("A folder with that name already exists") from exc
        return self._rows("SELECT *, 0 AS item_count FROM library_folders WHERE id = ?", (folder_id,))[0]

    def library_items(self, folder_id: str | None = None) -> list[dict]:
        where = "WHERE i.folder_id = ?" if folder_id else ""
        params = (folder_id,) if folder_id else ()
        rows = self._rows(f"""
            SELECT i.*, f.name AS folder_name, c.name AS chemical_name, s.title AS source_title, s.license AS source_license
            FROM library_items i JOIN library_folders f ON f.id = i.folder_id
            LEFT JOIN chemicals c ON c.id = i.chemical_id
            LEFT JOIN sources s ON s.id = i.source_id
            {where} ORDER BY i.updated_at DESC
        """, params)
        for row in rows:
            row["metadata"] = json.loads(row["metadata"] or "{}")
        return rows

    def _folder_exists(self, folder_id: str) -> bool:
        return bool(self._rows("SELECT id FROM library_folders WHERE id = ?", (folder_id,)))

    def create_library_item(self, payload: dict[str, Any]) -> dict:
        folder_id = payload["folder_id"]
        if not self._folder_exists(folder_id):
            raise ValueError("Library folder not found")
        item_id = "library-item-" + uuid.uuid4().hex[:14]
        timestamp = utc_now()
        with closing(self.connect()) as conn:
            if payload.get("chemical_id"):
                existing = conn.execute(
                    "SELECT id FROM library_items WHERE folder_id = ? AND item_type = 'chemical' AND chemical_id = ?",
                    (folder_id, payload["chemical_id"]),
                ).fetchone()
                if existing:
                    return self.library_items(folder_id)[0]
            conn.execute(
                "INSERT INTO library_items (id, folder_id, item_type, title, url, content, chemical_id, source_id, metadata, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (item_id, folder_id, payload["item_type"], payload["title"].strip(), payload.get("url", "").strip(), payload.get("content", ""), payload.get("chemical_id"), payload.get("source_id"), json.dumps(payload.get("metadata", {})), timestamp, timestamp),
            )
            conn.commit()
        return self.library_items(folder_id)[0]

    def save_live_chemical(self, record: dict[str, Any], folder_id: str) -> dict:
        if not self._folder_exists(folder_id):
            raise ValueError("Library folder not found")
        remote_id = str(record.get("id") or uuid.uuid4().hex)
        chemical_id = remote_id if remote_id.startswith("chem-") else "chem-live-" + remote_id.replace("/", "-")
        source = record.get("source") or {}
        source_id = source.get("id") or "source-live-" + uuid.uuid4().hex[:12]
        timestamp = utc_now()
        source_row = (
            source_id,
            source.get("title") or "Live public chemical source",
            source.get("source_type") or "public_database",
            source.get("publisher") or "Public chemistry database",
            source.get("url") or record.get("source_url") or "",
            source.get("license") or "Verify current source terms",
            source.get("accessed_at") or timestamp,
            json.dumps(source.get("metadata") or {"remote_id": remote_id}),
        )
        with closing(self.connect()) as conn:
            conn.execute("INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?,?,?,?)", source_row)
            conn.execute("""
                INSERT INTO chemicals (id, name, synonyms, cas_number, smiles, inchi, inchikey, formula, molecular_weight, description, structure_svg)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, synonyms=excluded.synonyms, cas_number=excluded.cas_number,
                smiles=excluded.smiles, inchi=excluded.inchi, inchikey=excluded.inchikey, formula=excluded.formula,
                molecular_weight=excluded.molecular_weight, description=excluded.description, structure_svg=excluded.structure_svg
            """, (
                chemical_id, record.get("name") or "Unnamed chemical", json.dumps(record.get("synonyms") or []),
                (record.get("cas_numbers") or [None])[0], record.get("smiles") or record.get("isomeric_smiles"),
                record.get("inchi"), record.get("inchikey"), record.get("formula"), record.get("molecular_weight"),
                record.get("subtitle") or "Live public chemical identity record", structure_svg(record.get("smiles") or record.get("isomeric_smiles")),
            ))
            numeric_properties = [
                ("xlogp", record.get("xlogp"), None),
                ("tpsa", record.get("tpsa"), "Å²"),
                ("exact_mass", record.get("exact_mass"), "Da"),
                ("monoisotopic_mass", record.get("monoisotopic_mass"), "Da"),
                ("h_bond_donors", record.get("h_bond_donors"), None),
                ("h_bond_acceptors", record.get("h_bond_acceptors"), None),
                ("rotatable_bonds", record.get("rotatable_bonds"), None),
                ("heavy_atoms", record.get("heavy_atoms"), None),
                ("formal_charge", record.get("charge"), None),
                ("complexity", record.get("complexity"), None),
                ("covalently_bonded_units", record.get("covalently_bonded_units"), None),
            ]
            for property_name, value, unit in numeric_properties:
                if value is None:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO property_values (id, chemical_id, property_name, value_text, numeric_value, unit, origin, source_id, method, confidence, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (f"pv-live-{chemical_id}-{property_name}", chemical_id, property_name, str(value), float(value), unit, "calculated", source_id, "PubChem computed property", None, "Retrieved from the cited PubChem compound record; verify current source semantics."),
                )
            if record.get("iupac_name"):
                conn.execute(
                    "INSERT OR REPLACE INTO property_values (id, chemical_id, property_name, value_text, numeric_value, unit, origin, source_id, method, confidence, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (f"pv-live-{chemical_id}-iupac_name", chemical_id, "iupac_name", record["iupac_name"], None, None, "literature_extracted", source_id, "PubChem identity field", None, "Identity nomenclature from the cited PubChem compound record."),
                )
            existing = conn.execute("SELECT id FROM library_items WHERE folder_id = ? AND item_type = 'chemical' AND chemical_id = ?", (folder_id, chemical_id)).fetchone()
            if existing:
                item_id = existing[0]
            else:
                item_id = "library-item-" + uuid.uuid4().hex[:14]
                conn.execute(
                    "INSERT INTO library_items (id, folder_id, item_type, title, url, content, chemical_id, source_id, metadata, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (item_id, folder_id, "chemical", record.get("name") or "Unnamed chemical", record.get("source_url") or source.get("url") or "", "", chemical_id, source_id, json.dumps({"remote_id": remote_id, "source": source}), timestamp, timestamp),
                )
            conn.commit()
        return self.library_items(folder_id)[0]

    def experiments(self) -> list[dict]:
        rows = self._rows("SELECT * FROM experiments ORDER BY updated_at DESC")
        for row in rows:
            row["chemical_ids"] = json.loads(row["chemical_ids"] or "[]")
        return rows

    def create_experiment(self, payload: dict[str, Any]) -> dict:
        exp_id = "exp-" + uuid.uuid4().hex[:10]
        timestamp = utc_now()
        started_at = payload.get("date") or timestamp[:10]
        chemical_ids = payload.get("chemical_ids") or []
        with closing(self.connect()) as conn:
            conn.execute(
                """INSERT INTO experiments (id, name, status, objective, chemical_ids, owner, started_at, updated_at, source_id, experiment_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    exp_id,
                    payload["name"].strip(),
                    payload.get("status", "planned"),
                    payload.get("objective", "").strip(),
                    json.dumps(chemical_ids),
                    payload.get("owner", "").strip() or "Lead Researcher",
                    started_at,
                    timestamp,
                    payload.get("source_id"),
                    payload.get("experiment_type", "Custom"),
                ),
            )
            conn.commit()
        rows = self._rows("SELECT * FROM experiments WHERE id = ?", (exp_id,))
        if rows:
            row = rows[0]
            row["chemical_ids"] = json.loads(row["chemical_ids"] or "[]")
            return row
        return {"id": exp_id, **payload}


    def formulations(self) -> list[dict]:
        rows = self._rows("SELECT * FROM formulations ORDER BY name")
        for row in rows:
            row["recipe"] = json.loads(row["recipe"] or "{}")
        return rows

    def reactions(self) -> list[dict]:
        rows = self._rows("SELECT * FROM reactions ORDER BY name")
        for row in rows:
            row["inputs"] = json.loads(row["inputs"] or "[]")
            row["outputs"] = json.loads(row["outputs"] or "[]")
            row["conditions"] = json.loads(row["conditions"] or "{}")
        return rows

    def analyses(self) -> list[dict]:
        rows = self._rows("SELECT id, experiment_id, technique, title, x_unit, y_unit, origin, source_id FROM analyses ORDER BY technique")
        return rows

    def analysis(self, analysis_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM analyses WHERE id = ?", (analysis_id,))
        if not rows:
            return None
        row = rows[0]
        payload = json.loads(row["series"])
        row["series"] = payload.get("series", payload) if isinstance(payload, dict) else payload
        return row

    def chemical_property_catalog(self) -> dict:
        """Return selectable numeric metrics without mixing value origins."""
        rows = self._rows("""
            SELECT c.id AS chemical_id, c.name, c.molecular_weight,
                   pv.id AS property_id, pv.property_name, pv.numeric_value,
                   pv.unit, pv.origin, pv.method, pv.source_id,
                   s.title AS source_title, s.license AS source_license
            FROM chemicals c
            LEFT JOIN property_values pv ON pv.chemical_id = c.id AND pv.numeric_value IS NOT NULL
            LEFT JOIN sources s ON s.id = pv.source_id
            ORDER BY c.name, pv.property_name, pv.origin, pv.id
        """)
        metrics: dict[str, dict[str, Any]] = {
            "identity:molecular_weight": {
                "key": "identity:molecular_weight", "label": "Molecular weight · identity",
                "property_name": "molecular_weight", "unit": "g/mol", "origin": "identity",
            }
        }
        points: dict[str, dict[str, Any]] = {}
        for row in rows:
            point = points.setdefault(row["chemical_id"], {"chemical_id": row["chemical_id"], "name": row["name"], "values": {}})
            if row["molecular_weight"] is not None:
                point["values"]["identity:molecular_weight"] = {
                    "value": row["molecular_weight"], "unit": "g/mol", "origin": "identity",
                    "source_title": "Chemical identity record", "source_license": "record-level identity",
                }
            if row["property_id"] is None or row["numeric_value"] is None:
                continue
            metric_key = f"property:{row['property_name']}:{row['origin']}:{row['unit'] or ''}"
            metrics.setdefault(metric_key, {
                "key": metric_key,
                "label": f"{row['property_name'].replace('_', ' ')} · {row['origin'].replace('_', ' ')}",
                "property_name": row["property_name"], "unit": row["unit"] or "", "origin": row["origin"],
            })
            point["values"].setdefault(metric_key, {
                "value": row["numeric_value"], "unit": row["unit"] or "", "origin": row["origin"],
                "source_title": row["source_title"] or "No source linked", "source_license": row["source_license"] or "",
                "method": row["method"] or "",
            })
        return {"metrics": list(metrics.values()), "points": list(points.values())}

    def search(self, query: str) -> list[dict]:
        q = f"%{query.lower()}%"
        chemicals = self._rows("SELECT id, name, 'chemical' AS type, description AS subtitle, formula FROM chemicals WHERE lower(name) LIKE ? OR lower(description) LIKE ? OR lower(synonyms) LIKE ?", (q, q, q))
        experiments = self._rows("SELECT id, name, 'experiment' AS type, objective AS subtitle, status AS formula FROM experiments WHERE lower(name) LIKE ? OR lower(objective) LIKE ?", (q, q))
        formulations = self._rows("SELECT id, name, 'formulation' AS type, purpose AS subtitle, status AS formula FROM formulations WHERE lower(name) LIKE ? OR lower(purpose) LIKE ?", (q, q))
        reactions = self._rows("SELECT id, name, 'reaction' AS type, reaction_type AS subtitle, yield_text AS formula FROM reactions WHERE lower(name) LIKE ? OR lower(reaction_type) LIKE ?", (q, q))
        return chemicals + experiments + formulations + reactions

    def graph(self) -> dict:
        chemicals = self.chemicals()
        nodes = []
        for c in chemicals:
            detail = self.chemical(c["id"]) or c
            nodes.append({
                "id": c["id"],
                "label": c["name"],
                "type": "chemical",
                "formula": detail.get("formula"),
                "smiles": detail.get("smiles"),
                "cas_number": detail.get("cas_number"),
                "structure_2d_url": detail.get("structure_2d_url"),
                "conformer_3d_url": detail.get("conformer_3d_url"),
            })
        edges = []
        for experiment in self.experiments():
            nodes.append({"id": experiment["id"], "label": experiment["name"], "type": "experiment"})
            for chemical_id in experiment["chemical_ids"]:
                edges.append({"source": experiment["id"], "target": chemical_id, "label": "studies"})
        for reaction in self.reactions():
            nodes.append({"id": reaction["id"], "label": reaction["name"], "type": "reaction"})
            for chemical_id in reaction["inputs"]:
                edges.append({"source": reaction["id"], "target": chemical_id, "label": "uses"})
            for output in reaction["outputs"]:
                if output.startswith("chem-"):
                    edges.append({"source": reaction["id"], "target": output, "label": "makes"})
        return {"nodes": nodes, "edges": edges}

    def dashboard(self) -> dict:
        counts = {}
        for table in ("chemicals", "experiments", "formulations", "reactions", "sources", "analyses"):
            counts[table] = self._rows(f"SELECT COUNT(*) AS count FROM {table}")[0]["count"]
        origin_counts = self._rows("SELECT origin, COUNT(*) AS count FROM property_values GROUP BY origin ORDER BY count DESC")
        return {"counts": counts, "origin_counts": origin_counts, "recent_sources": self.sources()[:4], "featured_chemicals": self.chemicals()[:4]}
