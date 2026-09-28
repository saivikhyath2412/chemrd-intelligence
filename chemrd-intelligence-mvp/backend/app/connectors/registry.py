from __future__ import annotations

from .base import DemoOpenDatasetConnector, LocalFileConnector

CONNECTORS = {
    item.info.id: item
    for item in (DemoOpenDatasetConnector(), LocalFileConnector())
}


def list_connectors() -> list[dict]:
    return [
        {
            "id": connector.info.id,
            "name": connector.info.name,
            "description": connector.info.description,
            "source_type": connector.info.source_type,
            "license_policy": connector.info.license_policy,
            "capabilities": list(connector.info.capabilities),
        }
        for connector in CONNECTORS.values()
    ]

