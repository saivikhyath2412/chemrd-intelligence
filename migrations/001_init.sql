-- PostgreSQL reference schema. The local MVP uses the equivalent SQLite schema
-- in backend/app/store.py so a first run needs no external service.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  source_type TEXT NOT NULL,
  publisher TEXT,
  url TEXT,
  license TEXT NOT NULL,
  accessed_at TIMESTAMPTZ,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS chemicals (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  synonyms JSONB NOT NULL DEFAULT '[]'::jsonb,
  cas_number TEXT,
  smiles TEXT,
  inchi TEXT,
  inchikey TEXT,
  formula TEXT,
  molecular_weight DOUBLE PRECISION,
  description TEXT,
  structure_svg TEXT,
  embedding vector(768)
);

CREATE TABLE IF NOT EXISTS property_values (
  id TEXT PRIMARY KEY,
  chemical_id TEXT NOT NULL REFERENCES chemicals(id),
  property_name TEXT NOT NULL,
  value_text TEXT NOT NULL,
  numeric_value DOUBLE PRECISION,
  unit TEXT,
  origin TEXT NOT NULL CHECK (origin IN ('measured','literature_extracted','calculated','model_predicted','ai_estimated')),
  source_id TEXT REFERENCES sources(id),
  method TEXT,
  confidence DOUBLE PRECISION,
  notes TEXT
);

CREATE INDEX IF NOT EXISTS property_values_chemical_idx ON property_values(chemical_id);
CREATE INDEX IF NOT EXISTS property_values_origin_idx ON property_values(origin);

CREATE TABLE IF NOT EXISTS library_folders (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS library_items (
  id TEXT PRIMARY KEY,
  folder_id TEXT NOT NULL REFERENCES library_folders(id) ON DELETE CASCADE,
  item_type TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL DEFAULT '',
  chemical_id TEXT REFERENCES chemicals(id),
  source_id TEXT REFERENCES sources(id),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS library_items_folder_idx ON library_items(folder_id);
CREATE INDEX IF NOT EXISTS library_items_chemical_idx ON library_items(chemical_id);
