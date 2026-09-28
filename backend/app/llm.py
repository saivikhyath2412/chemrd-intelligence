"""Provider fallback synthesis for the Research Assistant only.

The retrieval layer remains provider-neutral and citation-first. This module
receives a compact evidence pack and tries configured model providers in order:
OpenAI, Gemini, then Groq by default. A provider failure never hides the
retrieved evidence or exposes credentials.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_PROVIDER_ORDER = ("openai", "gemini", "groq")

_QUESTION_STOPWORDS = {
    "a", "an", "and", "are", "be", "can", "could", "does", "do", "for", "from",
    "give", "how", "i", "if", "in", "information", "is", "it", "me", "of", "on",
    "or", "please", "should", "tell", "that", "the", "this", "to", "what", "when",
    "where", "which", "who", "why", "with", "would", "you", "your", "about", "explain",
    "describe", "known", "everything", "compare", "find", "show", "used", "use", "using",
    "chemical", "chemicals", "compound", "compounds", "question", "answer",
}


def _value(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def configured() -> bool:
    return any(_value(name) for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"))


def _provider_order() -> list[str]:
    raw = _value("LLM_PROVIDER_ORDER", ",".join(DEFAULT_PROVIDER_ORDER))
    requested = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return [item for item in requested if item in {"openai", "gemini", "groq"}] or list(DEFAULT_PROVIDER_ORDER)


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9+.-]*", (value or "").casefold())
        if len(token) >= 2 and token not in _QUESTION_STOPWORDS
    }


def _record_text(item: dict[str, Any]) -> str:
    source = item.get("source", {}) or {}
    values = [
        item.get("name"), item.get("subtitle"), item.get("abstract"), item.get("iupac_name"),
        source.get("title"), source.get("publisher"), " ".join(item.get("synonyms", [])[:8]),
    ]
    return " ".join(str(value) for value in values if value)


def _relevance_score(question: str, item: dict[str, Any]) -> int:
    question_tokens = _tokens(question)
    record_tokens = _tokens(_record_text(item))
    overlap = question_tokens & record_tokens
    score = len(overlap)
    # A named chemical match is strong evidence even when the question is
    # short, e.g. "What is paclitaxel?".
    if item.get("type") == "live_chemical" and question_tokens:
        name_tokens = _tokens(str(item.get("name") or ""))
        if name_tokens and name_tokens <= question_tokens | record_tokens and name_tokens & question_tokens:
            score += 5
    return score


def relevant_evidence(question: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """Remove unrelated retrieval hits before they reach the model or UI.

    Public search providers often return plausible-looking but unrelated titles
    for broad questions. Those records must not become fake citations or force
    the assistant to refuse a perfectly answerable general question.
    """
    results = evidence.get("results", []) or []
    scored = [(index, _relevance_score(question, item), item) for index, item in enumerate(results)]
    selected = [item for _, score, item in scored if score > 0]
    selected.sort(key=lambda item: (0 if item.get("type") == "live_chemical" else 1))
    selected_ids = {id(item) for item in selected}
    citations = [
        citation for citation in (evidence.get("citations", []) or [])
        if any(
            citation.get("url") == item.get("source_url")
            or citation.get("id") == (item.get("source") or {}).get("id")
            for item in selected
        )
    ]
    filtered = dict(evidence)
    filtered["results"] = selected[:24]
    filtered["citations"] = citations[:8]
    filtered["evidence_quality"] = "relevant" if selected else "none"
    return filtered


def _compact_records(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep the prompt small while preserving the identity record and citations."""
    results = evidence.get("results", [])
    chemical = [item for item in results if item.get("type") == "live_chemical"]
    papers = [item for item in results if item.get("type") != "live_chemical"]
    max_sources = max(2, int(_value("CHEMRD_ASSISTANT_MAX_SOURCES", "7")))
    abstract_chars = max(300, int(_value("CHEMRD_ASSISTANT_ABSTRACT_CHARS", "900")))
    selected = (chemical + papers)[:max_sources]
    records: list[dict[str, Any]] = []
    for index, item in enumerate(selected, start=1):
        source = item.get("source", {}) or {}
        record = {
            "source_number": index,
            "type": item.get("type"),
            "name": item.get("name"),
            "subtitle": item.get("subtitle"),
            "url": item.get("source_url"),
            "source_title": source.get("title"),
            "publisher": source.get("publisher"),
            "license": source.get("license"),
        }
        if item.get("type") == "live_chemical":
            record.update({
                "formula": item.get("formula"),
                "molecular_weight": item.get("molecular_weight"),
                "iupac_name": item.get("iupac_name"),
                "inchikey": item.get("inchikey"),
                "inchi": item.get("inchi"),
                "smiles": item.get("smiles"),
                "isomeric_smiles": item.get("isomeric_smiles"),
                "cas_numbers": item.get("cas_numbers", []),
                "properties": {
                    "xlogp": item.get("xlogp"),
                    "tpsa": item.get("tpsa"),
                    "exact_mass": item.get("exact_mass"),
                    "monoisotopic_mass": item.get("monoisotopic_mass"),
                    "h_bond_donors": item.get("h_bond_donors"),
                    "h_bond_acceptors": item.get("h_bond_acceptors"),
                    "rotatable_bonds": item.get("rotatable_bonds"),
                    "heavy_atoms": item.get("heavy_atoms"),
                    "charge": item.get("charge"),
                    "complexity": item.get("complexity"),
                },
                "synonyms": item.get("synonyms", [])[:8],
            })
        else:
            record.update({
                "abstract": (item.get("abstract") or "")[:abstract_chars],
                "authors": item.get("authors", [])[:3],
            })
        records.append(record)
    return records


def _prompt_parts(question: str, evidence: dict[str, Any]) -> tuple[str, str]:
    records = _compact_records(evidence)
    evidence_json = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    instructions = (
        "You are the ChemR&D research assistant and a general chemistry explainer. "
        "Answer general scientific questions from your trained knowledge when the retrieved evidence is empty or unrelated. "
        "Always begin with a section titled 'Direct answer:' containing 2-4 concise sentences that directly answer the user's question. "
        "Then use a section titled 'Key facts:' for identifiers and molecular properties, when relevant, and a section titled 'Research findings:' for evidence from papers or patents. "
        "The direct answer must not be replaced by a bibliography or source list. "
        "Use retrieved sources only when they clearly relate to the named chemical or topic; ignore irrelevant search-result titles. "
        "If there are no relevant sources, answer normally and add a brief line saying 'General answer; no relevant source was retrieved for this response.' "
        "Do not invent a citation for a general-knowledge statement. "
        "A title alone is not evidence for a scientific claim. If the retrieved evidence is weak or unrelated, say that plainly. "
        "Never invent missing values. Separate PubChem identity facts from paper findings and label uncertainty. "
        "Cite evidence inline as [S1], [S2], etc., matching source_number. "
        "Do not give unsafe experimental instructions beyond the evidence."
    )
    prompt = f"User question:\n{question}\n\nRetrieved evidence:\n{evidence_json}"
    return instructions, prompt


def _post_json(url: str, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(exc.__class__.__name__) from exc


def _synthesize_openai(provider: str, instructions: str, prompt: str) -> str:
    from openai import OpenAI

    if provider == "openai":
        key = _value("OPENAI_API_KEY")
        model = _value("CHEMRD_ASSISTANT_MODEL", "gpt-5.4-mini")
        client = OpenAI(api_key=key, max_retries=0, timeout=30.0)
    else:
        key = _value("GROQ_API_KEY")
        model = _value("GROQ_MODEL", "openai/gpt-oss-120b")
        client = OpenAI(
            api_key=key,
            base_url=_value("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            max_retries=0,
            timeout=30.0,
        )
    response = client.responses.create(
        model=model,
        instructions=instructions,
        input=prompt,
        store=False,
        max_output_tokens=max(300, int(_value("CHEMRD_ASSISTANT_MAX_OUTPUT_TOKENS", "1200"))),
    )
    answer = (getattr(response, "output_text", "") or "").strip()
    if not answer:
        raise RuntimeError("empty response")
    return answer


def _synthesize_gemini(instructions: str, prompt: str) -> str:
    key = _value("GEMINI_API_KEY")
    model = _value("GEMINI_MODEL", "gemini-3.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model, safe='')}:generateContent?key={quote(key, safe='')}"
    payload = {
        "systemInstruction": {"parts": [{"text": instructions}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max(300, int(_value("CHEMRD_ASSISTANT_MAX_OUTPUT_TOKENS", "1200"))),
        },
    }
    response = _post_json(url, payload)
    parts = response.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    answer = "".join(part.get("text", "") for part in parts).strip()
    if not answer:
        raise RuntimeError("empty response")
    return answer


def synthesize(question: str, evidence: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Try configured providers in order; return answer, error summary, provider."""
    if not configured():
        return None, "No LLM provider API key is configured", None
    instructions, prompt = _prompt_parts(question, evidence)
    errors: list[str] = []
    for provider in _provider_order():
        key_name = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY"}[provider]
        if not _value(key_name):
            errors.append(f"{provider}: not configured")
            continue
        try:
            answer = _synthesize_gemini(instructions, prompt) if provider == "gemini" else _synthesize_openai(provider, instructions, prompt)
            return answer, None, provider
        except Exception as exc:
            errors.append(f"{provider}: {exc.__class__.__name__}")
    return None, "; ".join(errors) or "No provider was attempted", None
