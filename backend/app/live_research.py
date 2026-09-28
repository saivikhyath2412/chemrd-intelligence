"""Small, lawful public-source retrieval layer for open-ended research queries.

This module uses documented public APIs rather than scraping search-result HTML.
It deliberately returns the provider response as normalized evidence with a
source URL, access timestamp, and retrieval status so callers can distinguish
live observations from seeded or model-generated content.
"""

from __future__ import annotations

import json
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


def pubchem_lookup(query: str) -> tuple[dict[str, Any] | None, str | None]:
    candidate = _entity_candidate(query)
    properties = ",".join([
        "IUPACName", "MolecularFormula", "MolecularWeight", "CanonicalSMILES",
        "IsomericSMILES", "InChI", "InChIKey", "XLogP", "TPSA", "ExactMass",
        "MonoisotopicMass", "HBondDonorCount", "HBondAcceptorCount",
        "RotatableBondCount", "HeavyAtomCount", "Charge", "Complexity",
    ])
    url = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{}/property/{}/JSON".format(quote(candidate, safe=""), properties)
    payload, error = _fetch_json(url)
    property_rows = (payload or {}).get("PropertyTable", {}).get("Properties", [])
    if not property_rows:
        return None, error or "No chemical match"
    item = property_rows[0]
    cid = item.get("CID")
    synonyms_payload, synonyms_error = _fetch_json(
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
    )
    synonyms = (synonyms_payload or {}).get("InformationList", {}).get("Information", [{}])[0].get("Synonym", [])
    cas_numbers = [value for value in synonyms if re.fullmatch(r"\d{2,7}-\d{2}-\d", value)]
    return {
        "id": f"pubchem-{cid}",
        "type": "live_chemical",
        "name": candidate.title(),
        "subtitle": item.get("IUPACName") or "Chemical identity match",
        "formula": item.get("MolecularFormula"),
        "molecular_weight": item.get("MolecularWeight"),
        "smiles": item.get("ConnectivitySMILES") or item.get("CanonicalSMILES"),
        "isomeric_smiles": item.get("IsomericSMILES"),
        "inchi": item.get("InChI"),
        "inchikey": item.get("InChIKey"),
        "iupac_name": item.get("IUPACName"),
        "xlogp": item.get("XLogP"),
        "tpsa": item.get("TPSA"),
        "exact_mass": item.get("ExactMass"),
        "monoisotopic_mass": item.get("MonoisotopicMass"),
        "h_bond_donors": item.get("HBondDonorCount"),
        "h_bond_acceptors": item.get("HBondAcceptorCount"),
        "rotatable_bonds": item.get("RotatableBondCount"),
        "heavy_atoms": item.get("HeavyAtomCount"),
        "charge": item.get("Charge"),
        "complexity": item.get("Complexity"),
        "covalently_bonded_units": item.get("CovalentlyBondedUnitCount"),
        "cas_numbers": cas_numbers[:10],
        "synonyms": synonyms[:20],
        "source_url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}",
        "source": {
            "id": f"pubchem-source-{cid}",
            "title": f"PubChem compound record {cid}",
            "source_type": "public_database",
            "publisher": "NCBI PubChem",
            "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}",
            "license": "Public resource; verify current PubChem terms",
            "accessed_at": now_iso(),
            "metadata": {"cid": cid, "synonyms_status": "ok" if not synonyms_error else synonyms_error},
        },
    }, None


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
        # Do not send a generic research sentence to PubChem's name endpoint:
        # PubChem may return an unrelated low-CID match for arbitrary text.
        "PubChem": lambda: pubchem_lookup(query) if _identity_intent(query) or len(query.split()) <= 2 else (None, "skipped_non_identity_query"),
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
        answer = "Live public-source retrieval found a PubChem identity match.\n\n" + "\n".join(facts) + f"\n\nIt also found {len(papers)} potentially relevant literature records. Identity facts come from PubChem; article metadata and abstracts remain linked to their individual publishers or indexes."
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
