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
    remember_me: bool = False


class ProfileUpdateRequest(BaseModel):
    full_name: str = Field(default="", max_length=120)
    age: int | None = Field(default=None, ge=13, le=120)
    profile_picture: str | None = Field(default=None, max_length=900_000)
    research_field: str = Field(default="", max_length=160)
    organization: str = Field(default="", max_length=200)


class PreferencesUpdateRequest(BaseModel):
    settings: dict[str, Any]


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class ChemicalParam(BaseModel):
    id: str
    name: str
    formula: str | None = None
    cas_number: str | None = None
    smiles: str | None = None
    molecular_weight: float | None = None
    quantity: float | None = None
    quantity_unit: str = "g"
    role: str = "Reactant"
    temp_min: float = -20.0
    temp_max: float = 150.0
    pressure: float = 1.0
    ph: float = 7.0
    concentration: float = 100.0
    concentration_unit: str = "w/w"
    freezing_point: float | None = None
    melting_point: float | None = None
    boiling_point: float | None = None
    density: float | None = None
    molar_mass: float | None = None
    state_at_room_temp: str = "Solid"
    solubility: str = "Slightly Soluble"
    hazards: list[str] = Field(default_factory=list)
    cat_mechanism: str | None = None
    cat_ea_reduction: float | None = None
    cat_selectivity: str | None = None


class GlobalConditions(BaseModel):
    reaction_temp: float = 25.0
    duration: float = 1.0
    duration_unit: str = "hours"
    atmosphere: str = "Air"
    stirring_speed: float = 0.0
    heating_rate: float | None = None
    cooling_rate: float | None = None


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    objective: str = Field(min_length=1, max_length=2000)
    experiment_type: str = "Custom"
    owner: str = Field(default="", max_length=120)
    date: str = Field(default="")
    chemical_ids: list[str] = Field(default_factory=list)
    status: str = "planned"
    simulation_id: str | None = None


class SimulationRequest(BaseModel):
    name: str
    objective: str
    experiment_type: str = "Custom"
    chemicals: list[ChemicalParam]
    global_conditions: GlobalConditions


class SimulationSaveRequest(BaseModel):
    experiment: dict[str, Any]
    simulation_results: dict[str, Any]
