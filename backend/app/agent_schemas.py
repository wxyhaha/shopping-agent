from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.variant_schemas import VariantChoice


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def normalize_model_text(value: str) -> str:
    return value.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")


class StartResearchRequest(StrictModel):
    goal: str = Field(min_length=2, max_length=4000)


class ResumeResearchRequest(StrictModel):
    answer: str | None = Field(default=None, min_length=1, max_length=4000)


class ReadEvidenceArgs(StrictModel):
    item_id: str = Field(min_length=1, max_length=36)


class CaptureProductPageArgs(ReadEvidenceArgs):
    reason: str = Field(min_length=2, max_length=800)


class SelectProductVariantArgs(CaptureProductPageArgs):
    catalog_snapshot_id: str = Field(min_length=1, max_length=36)
    choices: list[VariantChoice] = Field(min_length=1, max_length=12)


class CollectionAvailability(BaseModel):
    available: bool
    browser_installed: bool
    parser_built: bool
    mode: Literal["headless"] = "headless"
    requires_open_product_page: Literal[False] = False


class AskUserArgs(StrictModel):
    question: str = Field(min_length=2, max_length=800)
    reason: str = Field(min_length=2, max_length=800)


class Finding(StrictModel):
    text: str = Field(min_length=1, max_length=1600)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)

    @field_validator("text")
    @classmethod
    def normalize_literal_line_breaks(cls, value: str) -> str:
        return normalize_model_text(value)


class ResearchReport(StrictModel):
    outcome: Literal["recommendation", "needs_evidence"] = "needs_evidence"
    summary: str = Field(min_length=1, max_length=2400)
    findings: list[Finding] = Field(min_length=1, max_length=20)
    unknowns: list[str] = Field(max_length=30)

    @field_validator("summary")
    @classmethod
    def normalize_summary_line_breaks(cls, value: str) -> str:
        return normalize_model_text(value)

    @field_validator("unknowns")
    @classmethod
    def normalize_unknown_line_breaks(cls, value: list[str]) -> list[str]:
        return [normalize_model_text(item) for item in value]


class ResearchEvent(BaseModel):
    id: str
    kind: str
    label: str
    data: dict
    elapsed_ms: int | None = None
    created_at: str


class ResearchRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    goal: str
    status: Literal[
        "queued", "running", "needs_input", "interrupted", "completed", "failed", "cancelled"
    ]
    model: str
    events: list[ResearchEvent]
    question: AskUserArgs | None
    report: ResearchReport | None
    error: str | None
    created_at: datetime
    updated_at: datetime
