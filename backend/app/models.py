from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ValueOrigin = Literal[
    "measured",
    "literature_extracted",
    "calculated",
    "model_predicted",
    "ai_estimated",
]


class AssistantRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    chemical_id: str | None = None
    live: bool = True


class IngestPreviewRequest(BaseModel):
    connector_id: str
    payload: dict = Field(default_factory=dict)


class LibraryFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class LibraryItemCreate(BaseModel):
    folder_id: str = Field(min_length=1, max_length=160)
    item_type: Literal["chemical", "paper", "patent", "website", "note", "experiment", "file", "other"]
    title: str = Field(min_length=1, max_length=240)
    url: str = Field(default="", max_length=2000)
    content: str = Field(default="", max_length=20000)
    chemical_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SaveLiveChemicalRequest(BaseModel):
    folder_id: str = Field(min_length=1, max_length=160)
    record: dict[str, Any]


class AuthRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=256)


class AuthLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)
