from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ConnectorInfo:
    id: str
    name: str
    description: str
    source_type: str
    license_policy: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)


class Connector(Protocol):
    info: ConnectorInfo

    def preview(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class DemoOpenDatasetConnector:
    info = ConnectorInfo(
        id="demo-open-dataset",
        name="Demo open dataset",
        description="Small normalized example connector for an approved open dataset.",
        source_type="open_dataset",
        license_policy="open_or_licensed_only",
        capabilities=("chemicals", "properties", "provenance"),
    )

    def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "connector": self.info.id,
            "status": "ready",
            "records_seen": int(payload.get("records", 3)),
            "policy": self.info.license_policy,
            "message": "Preview only: no records are persisted until an approved import job is run.",
        }


class LocalFileConnector:
    info = ConnectorInfo(
        id="local-file",
        name="Local CSV / JSON",
        description="Bring a locally owned or licensed export into the normalized model.",
        source_type="local_file",
        license_policy="user_supplied",
        capabilities=("chemicals", "experiments", "analyses"),
    )

    def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        filename = str(payload.get("filename", "untitled"))
        return {
            "connector": self.info.id,
            "status": "ready",
            "filename": filename,
            "records_seen": int(payload.get("records", 0)),
            "policy": self.info.license_policy,
            "message": "Local file accepted for validation; provenance and license fields are required on import.",
        }

