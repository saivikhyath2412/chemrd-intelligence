"""Small, lawful public-source retrieval layer for open-ended research queries.

This module uses documented public APIs rather than scraping search-result HTML.
It deliberately returns the provider response as normalized evidence with a
source URL, access timestamp, and retrieval status so callers can distinguish
live observations from seeded or model-generated content.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import threading
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, quote_plus
from urllib.request import Request, urlopen
from .chemistry import valid_smiles


USER_AGENT = "ChemRD-Intelligence/0.2 (research retrieval; contact configured by deployment)"
TIMEOUT_SECONDS = float(os.getenv("CHEMRD_LIVE_TIMEOUT", "8"))
CACHE_TTL_SECONDS = max(0, float(os.getenv("CHEMRD_LIVE_CACHE_TTL", "600")))
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()
_GENERIC_FOLLOW_UPS = {
    "tell me about it",
    "tell me about this",
    "tell me more",
    "what is it",
    "what is this",
    "explain it",
    "describe it",
    "more information",
    "more info",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_json(url: str) -> tuple[dict[str, Any] | None, str | None]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8")), None
    except HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return None, str(exc) or exc.__class__.__name__


def _fetch_text(url: str, accept: str = "text/plain") -> tuple[str | None, str | None]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8", errors="replace"), None
    except HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (URLError, TimeoutError, OSError) as exc:
        return None, str(exc) or exc.__class__.__name__


def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def _abstract_from_inverted(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    words: list[tuple[int, str]] = []
    for word, positions in index.items():
        words.extend((position, word) for position in positions)
    return " ".join(word for _, word in sorted(words))


def _entity_candidate(query: str) -> str:
    """Pick a likely compound phrase without pretending to understand all NLP."""
    raw = query.strip()
    if raw.startswith('InChI=') or re.fullmatch(r'(?:CHEBI:\d+|CHEMBL\d+|\d{2,7}-\d{2}-\d|[A-Z]{14}-[A-Z]{10}-[A-Z])', raw, re.I):
        return raw
    if not re.search(r'\b(?:what|how|tell|find|show|researching|properties|papers|articles)\b', raw, re.I):
        return raw.replace('β', 'beta').replace('α', 'alpha').replace('γ', 'gamma')
    subject = re.search(r'\bresearching\s+(.+?)(?:[.?]|$)', raw, re.I)
    if subject:
        return subject.group(1).strip()
    normalized = (
        query.replace("β", "beta").replace("Β", "Beta")
        .replace("α", "alpha").replace("Α", "Alpha")
        .replace("γ", "gamma").replace("Γ", "Gamma")
    )
    normalized = re.sub(r"[^A-Za-z0-9+\- ]", " ", normalized)
    words = [re.sub(r"^[+\-]+|[+\-]+$", "", word) for word in normalized.split()]
    words = [word for word in words if word]
    stop = {
        "what", "which", "where", "when", "why", "how", "tell", "about", "find", "search", "show",
        "me", "the", "this", "that", "and", "or", "for", "with", "from", "into", "between", "compare",
        "it", "describe", "explain", "give", "overview", "please",
        "information", "everything", "known", "properties", "property", "thermal", "stability", "analysis",
        "synthesis", "papers", "paper", "articles", "article", "research", "effects", "effect", "using",
        "used", "use", "resin", "resins", "acid",  # acid is retained below when paired with a name
    }
    meaningful = [word for word in words if len(word) > 2 and word.lower() not in stop]
    if not meaningful:
        return query.strip()
    # Preserve a Greek-prefix name even when users type it with a space.
    if meaningful[0].lower() in {"alpha", "beta", "gamma", "delta"} and len(meaningful) >= 2:
        return "-".join(meaningful[:2])
    # Preserve a common two-token chemical name such as "boric acid".
    if len(meaningful) >= 2 and words and words[0].lower() in {"boric", "benzoic", "salicylic", "acetic"}:
        return " ".join(meaningful[:2])
    return meaningful[0]


def _is_generic_follow_up(query: str) -> bool:
    normalized = re.sub(r"[^a-z0-9 ]", " ", query.casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized in _GENERIC_FOLLOW_UPS


def _identity_intent(query: str) -> bool:
    if valid_smiles(query.strip()):
        return True
    if len(query.split()) <= 8 and not re.search(r'[?]|\b(?:how|why|what|compare|papers|articles)\b', query, re.I):
        return True  # Multi-word chemical names and registry identifiers.
    words = set(re.findall(r"[a-z0-9]+", query.lower()))
    if words & {
        "identifier", "identifiers", "formula", "smiles", "inchi", "inchikey", "cas",
        "molecular", "properties", "property", "weight", "mass", "structure", "identity",
        "thermal", "stability", "synthesis", "compound", "chemical",
    }:
        return True
    candidate = _entity_candidate(query).lower()
    if candidate == query.strip().lower() or _is_generic_follow_up(query):
        return False
    # A conversational prefix followed by a concrete subject is still an
    # identity lookup, even when the name is not one of our seeded examples.
    return len(candidate) >= 4 and bool(
        words & {"what", "which", "tell", "about", "describe", "explain", "information"}
    )


def _rdkit_descriptors(smiles: str | None) -> dict[str, Any]:
    """Calculate descriptors locally and label them as calculated, never measured."""
    if not smiles:
        return {}
    try:
        from rdkit import Chem
        from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors

        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return {}
        return {
            "formula": rdMolDescriptors.CalcMolFormula(mol),
            "molecular_weight": round(Descriptors.MolWt(mol), 5),
            "exact_mass": round(Descriptors.ExactMolWt(mol), 5),
            "monoisotopic_mass": round(Descriptors.ExactMolWt(mol), 5),
            "xlogp": round(Crippen.MolLogP(mol), 5),
            "tpsa": round(rdMolDescriptors.CalcTPSA(mol), 5),
            "h_bond_donors": Lipinski.NumHDonors(mol),
            "h_bond_acceptors": Lipinski.NumHAcceptors(mol),
            "rotatable_bonds": Lipinski.NumRotatableBonds(mol),
            "heavy_atoms": Lipinski.HeavyAtomCount(mol),
            "charge": Chem.GetFormalCharge(mol),
            "complexity": round(Descriptors.BertzCT(mol), 5),
            "covalently_bonded_units": len(Chem.GetMolFrags(mol)),
            "atom_count": mol.GetNumAtoms(),
            "bond_count": mol.GetNumBonds(),
            "ring_count": rdMolDescriptors.CalcNumRings(mol),
            "aromatic_ring_count": rdMolDescriptors.CalcNumAromaticRings(mol),
            "fraction_csp3": round(rdMolDescriptors.CalcFractionCSP3(mol), 5),
            "molar_refractivity": round(Crippen.MolMR(mol), 5),
        }
    except Exception:
        return {}


def _first_nonempty(*values: Any) -> Any:
    return next((value for value in values if value not in (None, "", [], {})), None)


def _verified_chebi_lookup(candidate: str) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """Match names/aliases/accessions exactly; classes are never molecules."""
    base = 'https://www.ebi.ac.uk/chebi/backend/api/public'
    normalized = _strip_html(candidate).casefold().strip()
    if re.fullmatch(r'CHEBI:\d+', candidate, re.I):
        hits = [{'_source': {'chebi_accession': candidate.upper()}}]
        endpoints = []
    else:
        endpoints = [f'{base}/es_search/?term={quote(candidate, safe="")}&size=12']
        search, error = _fetch_json(endpoints[0])
        if error:
            return None, error, endpoints
        hits = (search or {}).get('results') or []
    sources = [h.get('_source', {}) for h in hits if isinstance(h, dict)]
    sources.sort(key=lambda s: 0 if any(_strip_html(str(s.get(k) or '')).casefold().strip() == normalized for k in ('name', 'ascii_name', 'chebi_name')) else 1)
    for source in sources:
        if source.get('smiles') and not valid_smiles(source['smiles']):
            continue
        accession = source.get('chebi_accession') or source.get('id')
        if not accession:
            continue
        cid = str(accession).split(':')[-1]
        url = f'{base}/compound/{cid}/'
        endpoints.append(url)
        detail, error = _fetch_json(url)
        if error or not detail:
            continue
        structure = detail.get('default_structure') or {}
        if not valid_smiles(structure.get('smiles')):
            continue
        synonyms = [str(item['name']) for group in (detail.get('names') or {}).values() if isinstance(group, list) for item in group if isinstance(item, dict) and item.get('name')]
        accessions = detail.get('database_accessions') or {}
        if isinstance(accessions, dict):
            for group in accessions.values():
                if isinstance(group, list):
                    synonyms.extend(str(item.get('accession_number') or item.get('accession')) for item in group if isinstance(item, dict) and (item.get('accession_number') or item.get('accession')))
        aliases = [detail.get('name'), detail.get('ascii_name'), source.get('name'), f'CHEBI:{cid}', structure.get('standard_inchi'), structure.get('standard_inchi_key'), *synonyms]
        if not any(_strip_html(str(n)).casefold().strip() == normalized for n in aliases if n):
            continue
        data = detail.get('chemical_data') or {}
        return {
            'name': _strip_html(detail.get('name') or source.get('name') or candidate),
            'definition': _strip_html(detail.get('definition') or detail.get('definition_text') or ''),
            'stars': detail.get('stars'),
            'formula': data.get('formula'), 'molecular_weight': data.get('mass'),
            'monoisotopic_mass': data.get('monoisotopic_mass'),
            'smiles': structure['smiles'], 'inchi': structure.get('standard_inchi'),
            'inchikey': structure.get('standard_inchi_key'),
            'cas_numbers': [n for n in synonyms if re.fullmatch(r'\d{2,7}-\d{2}-\d', n)],
            'synonyms': list(dict.fromkeys([candidate, *synonyms]))[:30],
            'chebi_id': f'CHEBI:{cid}',
        }, None, endpoints
    return None, 'No ChEBI match', endpoints


def _chebi_lookup(candidate: str) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    return _verified_chebi_lookup(candidate)


def _chembl_lookup(candidate: str) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """Best-effort ChEMBL enrichment for bioactive/drug-like molecules."""
    url = f"https://www.ebi.ac.uk/chembl/api/data/molecule/search.json?q={quote_plus(candidate)}&limit=8"
    payload, error = _fetch_json(url)
    if error:
        return None, error, [url]
    molecules = (payload or {}).get("molecules") or []
    normalized = candidate.casefold().strip()
    hit = next((item for item in molecules if any(str(n).casefold().strip() == normalized for n in [item.get('pref_name'), item.get('molecule_chembl_id'), (item.get('molecule_structures') or {}).get('standard_inchi_key'), *[s.get('molecule_synonym') for s in item.get('molecule_synonyms', [])]] if n) and valid_smiles((item.get('molecule_structures') or {}).get('canonical_smiles'))), None)
    if not hit:
        return None, "No ChEMBL match", [url]
    props = hit.get("molecule_properties") or {}
    structure = hit.get("molecule_structures") or {}
    chembl_id = hit.get("molecule_chembl_id")
    if not chembl_id:
        return None, "ChEMBL result has no molecule identifier", [url]
    return {
        "name": hit.get("pref_name") or candidate.title(),
        "chembl_id": chembl_id,
        "formula": props.get("full_molformula"),
        "molecular_weight": props.get("full_mwt"),
        "xlogp": props.get("alogp"),
        "h_bond_donors": props.get("hbd"),
        "h_bond_acceptors": props.get("hba"),
        "smiles": structure.get("canonical_smiles"),
        "inchi": structure.get("standard_inchi"),
        "inchikey": structure.get("standard_inchi_key"),
        "synonyms": [candidate, hit.get("pref_name")] if hit.get("pref_name") else [candidate],
    }, None, [url]


def _pubchem_lookup(candidate: str) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """Resolve an exact PubChem compound name/synonym to a verified structure."""
    encoded = quote(candidate, safe="")
    properties = "Title,IUPACName,MolecularFormula,MolecularWeight,SMILES,ConnectivitySMILES,InChI,InChIKey"
    property_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded}/property/{properties}/JSON"
    payload, error = _fetch_json(property_url)
    endpoints = [property_url]
    if error:
        return None, error, endpoints
    records = (payload or {}).get("PropertyTable", {}).get("Properties", [])
    normalized = candidate.casefold().strip()
    for item in records:
        cid = item.get("CID")
        if not cid:
            continue
        synonyms_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
        endpoints.append(synonyms_url)
        synonyms_payload, synonyms_error = _fetch_json(synonyms_url)
        synonyms = [str(value) for group in (synonyms_payload or {}).get("InformationList", {}).get("Information", []) for value in group.get("Synonym", []) if value]
        title = str(item.get("Title") or "")
        iupac = str(item.get("IUPACName") or "")
        # PUG REST may return a close/common-name result. Only accept the
        # requested exact name as a returned synonym or record title/name.
        if not any(name.casefold().strip() == normalized for name in [title, iupac, *synonyms]):
            continue
        smiles = item.get("SMILES") or item.get("ConnectivitySMILES")
        if not valid_smiles(smiles):
            continue
        cas_numbers = [name for name in synonyms if re.fullmatch(r"\d{2,7}-\d{2}-\d", name)]
        return {
            "name": title or candidate.title(),
            "pubchem_cid": int(cid),
            "iupac_name": iupac,
            "formula": item.get("MolecularFormula"),
            "molecular_weight": item.get("MolecularWeight"),
            "smiles": smiles,
            "inchi": item.get("InChI"),
            "inchikey": item.get("InChIKey"),
            "cas_numbers": cas_numbers,
            "synonyms": list(dict.fromkeys([candidate, title, *synonyms]))[:40],
            "synonyms_error": synonyms_error,
        }, None, endpoints
    return None, "No exact PubChem compound match", endpoints


def chemical_identity_lookup(query: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return a structured chemical record from the best public source.

    Resolution order is ChEBI -> ChEMBL -> PubChem -> OPSIN -> Cactus fallback.
    ChEBI and ChEMBL provide curated records; PubChem extends broad name and
    synonym coverage; OPSIN handles systematic names; Cactus is a final
    best-effort resolver. Every returned record must contain a valid molecule.
    """
    candidate = _entity_candidate(query)
    # Public identities cached on disk remain usable after an app restart or
    # provider outage. Never cache wildcard structures or failed lookups.
    from .structure_service import cache_dir
    identity_path = cache_dir() / ('identity-v4-' + hashlib.sha256(candidate.casefold().encode()).hexdigest() + '.json')
    try:
        cached = json.loads(identity_path.read_text(encoding='utf-8'))
        if (str(cached.get('query_name') or '').casefold().strip() == candidate.casefold().strip()
                and valid_smiles(cached.get('smiles'))
                and time.time() - identity_path.stat().st_mtime < 30 * 86400):
            cached['cache_hit'] = True
            return cached, None
    except (OSError, ValueError):
        pass
    encoded = quote(candidate, safe="")
    provider_errors: list[str] = []
    smiles = inchi = inchikey = None
    cactus_names: list[str] = []
    used_provider = None
    endpoints: list[str] = []

    # Primary: curated ChEBI identity and structure.
    chebi_record = None
    if valid_smiles(candidate) or candidate.startswith('InChI='):
        from rdkit import Chem
        mol = Chem.MolFromSmiles(candidate) if valid_smiles(candidate) else Chem.MolFromInchi(candidate)
        if mol is not None:
            smiles = Chem.MolToSmiles(mol, isomericSmiles=True)
            inchi = Chem.MolToInchi(mol)
            inchikey = Chem.MolToInchiKey(mol)
            used_provider = 'RDKit (supplied molecular structure)'
    chebi_record, error, chebi_endpoints = (None, None, []) if smiles else _chebi_lookup(candidate)
    endpoints.extend(chebi_endpoints)
    if error:
        provider_errors.append(f"ChEBI: {error}")
    elif chebi_record:
        used_provider = "EMBL-EBI ChEBI"
        smiles = chebi_record.get("smiles")
        inchi = chebi_record.get("inchi")
        inchikey = chebi_record.get("inchikey")

    chembl_record = None
    if not smiles:
        chembl_record, error, chembl_endpoints = _chembl_lookup(candidate)
        endpoints.extend(chembl_endpoints)
        if error:
            provider_errors.append(f"ChEMBL: {error}")
        elif chembl_record and chembl_record.get("smiles"):
            used_provider = "EMBL-EBI ChEMBL"
            smiles = chembl_record.get("smiles")
            inchi = _first_nonempty(inchi, chembl_record.get("inchi"))
            inchikey = _first_nonempty(inchikey, chembl_record.get("inchikey"))

    pubchem_record = None
    if not smiles:
        pubchem_record, error, pubchem_endpoints = _pubchem_lookup(candidate)
        endpoints.extend(pubchem_endpoints)
        if error:
            provider_errors.append(f"PubChem: {error}")
        elif pubchem_record and valid_smiles(pubchem_record.get("smiles")):
            used_provider = "PubChem"
            smiles = pubchem_record.get("smiles")
            inchi = _first_nonempty(inchi, pubchem_record.get("inchi"))
            inchikey = _first_nonempty(inchikey, pubchem_record.get("inchikey"))

    # Enrichment/fallback: OPSIN handles systematic names and can provide an
    # IUPAC name even when ChEBI only exposes a preferred common name.
    opsin_payload = None
    if not smiles:
        opsin_url = f"https://www.ebi.ac.uk/opsin/ws/{encoded}.json"
        opsin_payload, error = _fetch_json(opsin_url)
        endpoints.append(opsin_url)
        if error:
            provider_errors.append(f"OPSIN: {error}")
        elif opsin_payload and valid_smiles(opsin_payload.get("smiles")):
            used_provider = used_provider if smiles else "OPSIN"
            smiles = smiles or opsin_payload.get("smiles")
            inchi = _first_nonempty(inchi, opsin_payload.get("stdinchi"), opsin_payload.get("inchi"))
            inchikey = _first_nonempty(inchikey, opsin_payload.get("stdinchikey"))

    # Last resort only: retain broad name coverage without making Cactus the
    # normal destination for a search result.
    if not smiles:
        cactus_base = f"https://cactus.nci.nih.gov/chemical/structure/{encoded}"
        for representation, target in (("smiles", "smiles"), ("stdinchi", "inchi"), ("stdinchikey", "inchikey"), ("names", "names")):
            url = f"{cactus_base}/{representation}"
            text, error = _fetch_text(url)
            endpoints.append(url)
            if error:
                provider_errors.append(f"Cactus fallback {representation}: {error}")
                continue
            value = (text or "").strip()
            if not value:
                continue
            used_provider = "NCI/CADD Cactus (fallback)"
            if target == "smiles":
                smiles = value.splitlines()[0].strip()
                if not valid_smiles(smiles):
                    smiles = None
            elif target == "inchi":
                inchi = value.splitlines()[0].strip()
            elif target == "inchikey":
                inchikey = value.splitlines()[0].strip()
            else:
                cactus_names = [line.strip() for line in value.splitlines() if line.strip()][:40]

    if not valid_smiles(smiles):
        return None, "; ".join(provider_errors) or "No chemical match"

    calculated = _rdkit_descriptors(smiles)
    names = [candidate] + ((chebi_record or {}).get("synonyms") or []) + ((chembl_record or {}).get("synonyms") or []) + ((pubchem_record or {}).get("synonyms") or []) + cactus_names
    if opsin_payload and opsin_payload.get("name"):
        names.append(str(opsin_payload["name"]))
    synonyms = list(dict.fromkeys(name for name in names if name))[:20]
    cas_numbers = list(dict.fromkeys(((chebi_record or {}).get("cas_numbers") or []) + [value for value in synonyms if re.fullmatch(r"\d{2,7}-\d{2}-\d", value)]))
    normalized_id = hashlib.sha1((inchikey or smiles or candidate).encode("utf-8")).hexdigest()[:16]
    # Keep API endpoints in provenance metadata, but use a human-facing page
    # for the clickable source link. Otherwise ChEBI opens its raw JSON/API
    # response (for example, the "Compound Detail API" page) in the browser.
    chebi_id = (chebi_record or {}).get("chebi_id")
    source_url = (
        f"https://www.ebi.ac.uk/chebi/searchId.do?chebiId={quote(chebi_id, safe=':')}" if chebi_id and used_provider == "EMBL-EBI ChEBI"
        else f"https://www.ebi.ac.uk/chembl/compound_report_card/{(chembl_record or {}).get('chembl_id')}/" if used_provider == "EMBL-EBI ChEMBL" and (chembl_record or {}).get("chembl_id")
        else f"https://pubchem.ncbi.nlm.nih.gov/compound/{(pubchem_record or {}).get('pubchem_cid')}" if used_provider == "PubChem" and (pubchem_record or {}).get("pubchem_cid")
        else f"https://www.ebi.ac.uk/opsin/" if used_provider == "OPSIN"
        else "https://www.rdkit.org/docs/GettingStartedInPython.html" if used_provider == 'RDKit (supplied molecular structure)'
        else f"https://cactus.nci.nih.gov/chemical/structure/{encoded}/"
    )
    # Cactus names are synonyms; only OPSIN's normalized name is promoted to
    # the IUPAC field so a synonym is never silently mislabeled.
    iupac_name = _first_nonempty((opsin_payload or {}).get("name"), (pubchem_record or {}).get("iupac_name"))
    display_name = _first_nonempty((chebi_record or {}).get("name"), (chembl_record or {}).get("name"), (pubchem_record or {}).get("name"), (opsin_payload or {}).get("name"), candidate.title())
    formula = _first_nonempty((chebi_record or {}).get("formula"), (chembl_record or {}).get("formula"), (pubchem_record or {}).get("formula"), (opsin_payload or {}).get("formula"), calculated.get("formula"))
    molecular_weight = _first_nonempty((chebi_record or {}).get("molecular_weight"), (chembl_record or {}).get("molecular_weight"), (pubchem_record or {}).get("molecular_weight"), (opsin_payload or {}).get("molecularWeight"), calculated.get("molecular_weight"))
    properties = [
        {"property_name": "formula", "label": "Molecular formula", "value": formula, "unit": "", "origin": "public_database" if (chebi_record and chebi_record.get("formula")) or (chembl_record and chembl_record.get("formula")) or (pubchem_record and pubchem_record.get("formula")) else "calculated"},
        {"property_name": "molecular_weight", "label": "Molecular weight", "value": molecular_weight, "unit": "g/mol", "origin": "public_database" if (chebi_record and chebi_record.get("molecular_weight")) or (chembl_record and chembl_record.get("molecular_weight")) or (pubchem_record and pubchem_record.get("molecular_weight")) else "calculated"},
        {"property_name": "exact_mass", "label": "Exact mass", "value": calculated.get("exact_mass"), "unit": "Da", "origin": "calculated"},
        {"property_name": "xlogp", "label": "XlogP", "value": calculated.get("xlogp"), "unit": "", "origin": "calculated"},
        {"property_name": "tpsa", "label": "Topological polar surface area", "value": calculated.get("tpsa"), "unit": "Å²", "origin": "calculated"},
        {"property_name": "h_bond_donors", "label": "H-bond donors", "value": calculated.get("h_bond_donors"), "unit": "", "origin": "calculated"},
        {"property_name": "h_bond_acceptors", "label": "H-bond acceptors", "value": calculated.get("h_bond_acceptors"), "unit": "", "origin": "calculated"},
        {"property_name": "rotatable_bonds", "label": "Rotatable bonds", "value": calculated.get("rotatable_bonds"), "unit": "", "origin": "calculated"},
        {"property_name": "heavy_atoms", "label": "Heavy atoms", "value": calculated.get("heavy_atoms"), "unit": "", "origin": "calculated"},
        {"property_name": "charge", "label": "Formal charge", "value": calculated.get("charge"), "unit": "e", "origin": "calculated"},
    ]
    properties = [item for item in properties if item["value"] not in (None, "")]
    record = {
        "id": f"chemical-{normalized_id}",
        "type": "live_chemical",
        "name": display_name,
        "query_name": candidate,
        "subtitle": iupac_name or f"{used_provider or 'Public chemistry'} · {chebi_id or 'structured identity match'}",
        "description": (chebi_record or {}).get("definition") or "Structured chemical identity record with source-linked properties.",
        "definition": (chebi_record or {}).get("definition"),
        "formula": formula,
        "molecular_weight": molecular_weight,
        "smiles": smiles,
        "isomeric_smiles": smiles,
        "inchi": inchi,
        "inchikey": inchikey,
        "iupac_name": iupac_name,
        "properties": properties,
        "identifiers": {"chebi_id": chebi_id, "chembl_id": (chembl_record or {}).get("chembl_id"), "pubchem_cid": (pubchem_record or {}).get("pubchem_cid"), "cas_numbers": cas_numbers[:10], "inchikey": inchikey, "inchi": inchi},
        "source_links": {
            "primary": source_url,
            "chebi": f"https://www.ebi.ac.uk/chebi/searchId.do?chebiId={quote(chebi_id, safe=':')}" if chebi_id else None,
            "chembl": f"https://www.ebi.ac.uk/chembl/compound_report_card/{(chembl_record or {}).get('chembl_id')}/" if (chembl_record or {}).get("chembl_id") else None,
            "pubchem": f"https://pubchem.ncbi.nlm.nih.gov/compound/{(pubchem_record or {}).get('pubchem_cid')}" if (pubchem_record or {}).get("pubchem_cid") else None,
            "opsin": f"https://www.ebi.ac.uk/opsin/" if opsin_payload else None,
            "cactus_fallback": f"https://cactus.nci.nih.gov/chemical/structure/{encoded}/" if used_provider == "NCI/CADD Cactus (fallback)" else None,
        },
        **calculated,
        "cas_numbers": cas_numbers[:10],
        "synonyms": synonyms,
        "source_url": source_url,
        "source": {
            "id": f"chemical-source-{normalized_id}",
            "title": f"{used_provider or 'Public chemistry'} identity record",
            "source_type": "public_database",
            "publisher": used_provider or "Public chemistry provider",
            "url": source_url,
            "license": "Public service; verify current provider terms before redistribution",
            "accessed_at": now_iso(),
            "metadata": {
                "provider": used_provider,
                "provider_order": ["EMBL-EBI ChEBI", "EMBL-EBI ChEMBL", "PubChem", "OPSIN", "NCI/CADD Cactus (fallback)"],
                "chebi_id": chebi_id,
                "endpoints": endpoints,
                "descriptor_origin": "calculated locally with RDKit" if calculated else "not calculated",
                "provider_errors": provider_errors,
            },
        },
    }
    try:
        import uuid
        temporary = identity_path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(record), encoding='utf-8')
        temporary.replace(identity_path)
    except OSError:
        pass
    return record, None


def openalex_search(query: str) -> tuple[list[dict[str, Any]], str | None]:
    url = f"https://api.openalex.org/works?search={quote_plus(query)}&per-page=5&sort=relevance_score:desc"
    payload, error = _fetch_json(url)
    results = []
    for item in (payload or {}).get("results", []):
        title = item.get("title") or "Untitled work"
        doi = item.get("doi") or ""
        landing = item.get("primary_location", {}).get("landing_page_url") or doi or item.get("id")
        results.append({
            "id": f"openalex-{item.get('id', '').split('/')[-1]}",
            "type": "live_paper",
            "name": title,
            "subtitle": f"OpenAlex · {item.get('publication_year') or 'year unknown'}",
            "abstract": _abstract_from_inverted(item.get("abstract_inverted_index")),
            "authors": [a.get("author", {}).get("display_name") for a in item.get("authorships", [])[:4]],
            "source_url": landing,
            "source": {
                "id": f"openalex-source-{item.get('id', '').split('/')[-1]}",
                "title": title,
                "source_type": "paper",
                "publisher": "OpenAlex",
                "url": landing,
                "license": "Open scholarly metadata; full text depends on host license",
                "accessed_at": now_iso(),
                "metadata": {"doi": doi, "open_access": item.get("open_access", {}).get("is_oa", False)},
            },
        })
    return results, error


def crossref_search(query: str) -> tuple[list[dict[str, Any]], str | None]:
    url = f"https://api.crossref.org/works?query.bibliographic={quote_plus(query)}&rows=4&select=DOI,title,author,published,URL,publisher,abstract,license"
    payload, error = _fetch_json(url)
    results = []
    for item in (payload or {}).get("message", {}).get("items", []):
        title = (item.get("title") or ["Untitled work"])[0]
        published = item.get("published", {}).get("date-parts", [[None]])[0]
        doi = item.get("DOI")
        landing = item.get("URL") or (f"https://doi.org/{doi}" if doi else "")
        results.append({
            "id": f"crossref-{doi or re.sub(r'[^a-z0-9]+', '-', title.lower())[:50]}",
            "type": "live_paper",
            "name": title,
            "subtitle": f"Crossref · {published[0] if published else 'year unknown'}",
            "abstract": _strip_html(item.get("abstract")),
            "authors": [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in item.get("author", [])[:4]],
            "source_url": landing,
            "source": {
                "id": f"crossref-source-{doi or title[:24]}",
                "title": title,
                "source_type": "paper",
                "publisher": item.get("publisher") or "Crossref",
                "url": landing,
                "license": "Metadata from Crossref; article access depends on publisher license",
                "accessed_at": now_iso(),
                "metadata": {"doi": doi, "licenses": item.get("license", [])},
            },
        })
    return results, error


def europe_pmc_search(query: str) -> tuple[list[dict[str, Any]], str | None]:
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={quote_plus(query)}&format=json&pageSize=4&resultType=core"
    payload, error = _fetch_json(url)
    results = []
    for item in (payload or {}).get("resultList", {}).get("result", []):
        title = item.get("title") or "Untitled biomedical work"
        doi = item.get("doi")
        landing = f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/{item.get('source', 'MED')}/{item.get('id', '')}"
        results.append({
            "id": f"europepmc-{item.get('id', '')}",
            "type": "live_paper",
            "name": title,
            "subtitle": f"Europe PMC · {item.get('pubYear') or 'year unknown'}",
            "abstract": item.get("abstractText") or "",
            "authors": [a.get("fullName") for a in item.get("authorList", {}).get("author", [])[:4]],
            "source_url": landing,
            "source": {
                "id": f"europepmc-source-{item.get('id', '')}",
                "title": title,
                "source_type": "paper",
                "publisher": "Europe PMC",
                "url": landing,
                "license": "Europe PMC metadata and full text subject to source license",
                "accessed_at": now_iso(),
                "metadata": {"doi": doi, "pmcid": item.get("pmcid"), "is_open_access": item.get("isOpenAccess")},
            },
        })
    return results, error


def _dedupe(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output = []
    for item in results:
        key = (item.get("source_url") or item.get("name", "")).lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def live_research(query: str) -> dict[str, Any]:
    """Retrieve a compact evidence pack from public APIs for an arbitrary query."""
    query = query.strip()
    if len(query) < 3:
        return {"query": query, "results": [], "citations": [], "answer": "Please enter at least three characters.", "providers": {}}
    if _is_generic_follow_up(query):
        return {
            "query": query,
            "results": [],
            "citations": [],
            "answer": "Please include the chemical, material, paper, or research topic you mean. The assistant does not yet infer what \"it\" refers to from a previous chat message.",
            "providers": {},
            "retrieved_at": now_iso(),
            "policy": "documented_public_apis_only; full text remains subject to source license",
        }
    cache_key = query.casefold()
    if CACHE_TTL_SECONDS:
        with _CACHE_LOCK:
            cached = _CACHE.get(cache_key)
            if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
                response = deepcopy(cached[1])
                response["cache_hit"] = True
                return response
    # Identity/property questions are searched by the likely compound name so
    # article providers do not waste results on generic words like "properties".
    provider_query = _entity_candidate(query) if _identity_intent(query) else query
    jobs = {
        # Do not send a generic research sentence to a chemical name resolver:
        # identity providers can return an unrelated match for arbitrary text.
        "Chemical identity (ChEBI → ChEMBL → PubChem → OPSIN → Cactus)": lambda: chemical_identity_lookup(query) if _identity_intent(query) or len(query.split()) <= 2 else (None, "skipped_non_identity_query"),
        "OpenAlex": lambda: openalex_search(provider_query),
        "Crossref": lambda: crossref_search(provider_query),
        "Europe PMC": lambda: europe_pmc_search(provider_query),
    }
    results: list[dict[str, Any]] = []
    providers: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fn): name for name, fn in jobs.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                payload, error = future.result()
                if isinstance(payload, list):
                    results.extend(payload)
                    count = len(payload)
                elif payload:
                    results.append(payload)
                    count = 1
                else:
                    count = 0
                providers[name] = {"status": "ok" if not error else "partial", "records": count, "error": error}
            except Exception as exc:  # a provider must never break the whole search
                providers[name] = {"status": "error", "records": 0, "error": str(exc)}
    results = _dedupe(results)
    # Keep the identity record first, then the most relevant literature order
    # returned by each provider. This makes [S1] the chemistry identity source.
    results.sort(key=lambda item: 0 if item.get("type") == "live_chemical" else 1)
    chemical_matches = [r for r in results if r["type"] == "live_chemical"]
    papers = [r for r in results if r["type"] == "live_paper"]
    if chemical_matches:
        chem = chemical_matches[0]
        facts = [
            f"Name: {chem['name']}",
            f"Formula: {chem.get('formula') or 'not reported'}",
            f"Molecular weight: {chem.get('molecular_weight') or 'not reported'} g/mol",
            f"IUPAC name: {chem.get('iupac_name') or 'not reported'}",
            f"InChIKey: {chem.get('inchikey') or 'not reported'}",
            f"Canonical SMILES: {chem.get('smiles') or 'not reported'}",
        ]
        if chem.get("cas_numbers"):
            facts.append(f"CAS identifiers: {', '.join(chem['cas_numbers'])}")
        provider = (chem.get("source") or {}).get("publisher") or "the configured chemical identity providers"
        answer = f"Live public-source retrieval found a chemical identity match via {provider}.\n\n" + "\n".join(facts) + f"\n\nIt also found {len(papers)} potentially relevant literature records. Identity facts and locally calculated descriptors are kept separate from article metadata and abstracts, which remain linked to their individual publishers or indexes."
    elif papers:
        answer = f"Live public-source retrieval found {len(papers)} potentially relevant literature records for “{query}”. Results are metadata/abstract-level evidence and should be opened at the cited source before relying on a claim."
    else:
        answer = f"Live retrieval did not find a normalized chemical identity or article result for “{query}”. Try a more specific compound name, identifier, DOI, or research phrase."
    citations = [
        {"id": item["source"]["id"], "label": item["source"]["title"], "url": item.get("source_url"), "reason": item["type"].replace("live_", "")}
        for item in results[:8]
    ]
    response = {
        "query": query,
        "results": results[:24],
        "citations": citations,
        "answer": answer,
        "providers": providers,
        "retrieved_at": now_iso(),
        "policy": "documented_public_apis_only; full text remains subject to source license",
    }
    if CACHE_TTL_SECONDS:
        with _CACHE_LOCK:
            _CACHE[cache_key] = (time.monotonic(), deepcopy(response))
    return response
