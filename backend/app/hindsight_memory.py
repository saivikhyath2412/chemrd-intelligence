"""Optional Hindsight memory integration for the Research Assistant.

Hindsight memory is continuity context, not scientific evidence. The adapter
therefore returns it in a separate field and never turns it into a citation.
It uses the documented REST endpoints directly so the desktop executable does
not require an additional Python package.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - bundled app includes python-dotenv
    load_dotenv = None


def _value(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


class HindsightMemory:
    def _load_runtime_env(self) -> None:
        """Reload the user-managed .env from common desktop/source locations."""
        if load_dotenv is None:
            return
        paths = [Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"]
        if getattr(sys, "frozen", False):
            paths.insert(0, Path(sys.executable).resolve().parent / ".env")
        for path in paths:
            if path.is_file():
                load_dotenv(path, override=False)

    def enabled(self) -> bool:
        self._load_runtime_env()
        return _value("HINDSIGHT_ENABLED", "false").lower() in {"1", "true", "yes"} and bool(_value("HINDSIGHT_API_URL"))

    def status(self) -> str:
        self._load_runtime_env()
        if not self.enabled():
            return "disabled"
        return "configured"

    def _bank_id(self, user_id: str) -> str:
        prefix = re.sub(r"[^A-Za-z0-9_-]+", "-", _value("HINDSIGHT_BANK_PREFIX", "chemrd"))[:40].strip("-") or "chemrd"
        safe_user = re.sub(r"[^A-Za-z0-9_-]+", "-", user_id)[:80].strip("-") or "user"
        return f"{prefix}-{safe_user}"

    def _request(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        base = _value("HINDSIGHT_API_URL").rstrip("/")
        request = Request(
            f"{base}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {_value('HINDSIGHT_API_KEY')}"} if _value("HINDSIGHT_API_KEY") else {}),
            },
            method=method,
        )
        timeout = max(1.0, float(_value("HINDSIGHT_TIMEOUT", "4")))
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        return json.loads(body) if body else {}

    def _ensure_bank(self, user_id: str) -> None:
        """Create/update the user's bank so first use needs no manual setup."""
        bank = quote(self._bank_id(user_id), safe="")
        self._request(
            "PUT",
            f"/v1/default/banks/{bank}",
            {
                "name": self._bank_id(user_id),
                "observations_mission": "Remember stable preferences and project context for this ChemR&D account.",
                "retain_mission": "Remember useful ChemR&D assistant conversation context; do not treat it as scientific evidence.",
            },
        )

    def recall(self, user_id: str, query: str) -> dict[str, Any]:
        empty = {"items": [], "status": self.status()}
        if not self.enabled() or not user_id or not query.strip():
            return empty
        bank = quote(self._bank_id(user_id), safe="")
        payload = {
            "query": query[:2000],
            "budget": _value("HINDSIGHT_RECALL_BUDGET", "low"),
            "max_tokens": max(200, int(_value("HINDSIGHT_RECALL_MAX_TOKENS", "1200"))),
            "types": ["world", "experience", "observation"],
            "prefer_observations": True,
        }
        try:
            self._ensure_bank(user_id)
            response = self._request("POST", f"/v1/default/banks/{bank}/memories/recall", payload)
            items = []
            for item in response.get("results", []) or []:
                if not isinstance(item, dict) or not item.get("text"):
                    continue
                items.append({
                    "id": item.get("id"),
                    "text": str(item.get("text"))[:3000],
                    "type": item.get("type", "experience"),
                    "context": item.get("context"),
                    "metadata": item.get("metadata") or {},
                })
            return {"items": items[:8], "status": "ok" if items else "ok_empty"}
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return {"items": [], "status": "unavailable"}

    def retain_turn(self, user_id: str, question: str, answer: str, source_count: int = 0) -> str:
        if not self.enabled() or not user_id or not question.strip() or not answer.strip():
            return self.status()
        bank = quote(self._bank_id(user_id), safe="")
        user_tag = f"user:{user_id}"
        document_id = f"assistant-turn-{uuid.uuid4().hex}"
        payload = {
            "items": [{
                "content": f"User: {question.strip()}\nAssistant: {answer.strip()[:12000]}",
                "context": "ChemR&D research assistant conversation",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "document_id": document_id,
            }],
            # Conversation turns are small; synchronous processing in the
            # background worker makes the next assistant request recallable
            # immediately after the first response has completed.
            "async": False,
        }
        try:
            self._ensure_bank(user_id)
            self._request("POST", f"/v1/default/banks/{bank}/memories", payload)
            return "queued"
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return "unavailable"


hindsight_memory = HindsightMemory()
