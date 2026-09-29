"""Small privacy helpers for persisted research-assistant context."""

from __future__ import annotations

import re


_SECRET_ASSIGNMENT = re.compile(
    r"(?im)(\b(?:[A-Z0-9]+[_-])*(?:API[_ -]?KEY|ACCESS[_ -]?TOKEN|REFRESH[_ -]?TOKEN|"
    r"AUTH[_ -]?TOKEN|CLIENT[_ -]?SECRET|PASSWORD|PASSWD|SECRET|CREDENTIALS?|AUTHORIZATION)\b"
    r"\s*[:=]\s*)[^\r\n]*"
)
_BEARER_SECRET = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_COMMON_TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|hsk_[A-Za-z0-9_-]{12,}|AIza[0-9A-Za-z_-]{20,})\b")


def redact_sensitive_text(value: str) -> str:
    """Remove obvious credential values before conversation text is persisted."""
    text = str(value or "")
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = _BEARER_SECRET.sub("Bearer [REDACTED]", text)
    return _COMMON_TOKEN.sub("[REDACTED]", text)
