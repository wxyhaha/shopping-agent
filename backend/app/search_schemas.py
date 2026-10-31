"""Bounded search inputs and explicit alternative-product assessments."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HardCondition(SearchModel):
    id: str = Field(pattern=r"^c[1-9][0-9]?$", max_length=3)
    description: str = Field(min_length=2, max_length=400)


class SearchRequirementsArgs(SearchModel):
    source_item_id: str = Field(min_length=1, max_length=36)
    hard_conditions: list[HardCondition] = Field(min_length=1, max_length=10)
    preferences: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def unique_conditions(self):
        if len({entry.id for entry in self.hard_conditions}) != len(self.hard_conditions):
            raise ValueError("condition IDs must be unique")
        if any(not value.strip() or len(value) > 400 for value in self.preferences):
            raise ValueError("invalid preference")
        return self


class SearchProductsArgs(SearchModel):
    platform: Literal["taobao", "jd"]
    query: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=2, max_length=500)


class SearchCandidate(SearchModel):
    id: str = Field(pattern=r"^candidate-[a-f0-9]{16}$")
    platform: Literal["taobao", "tmall", "jd"]
    url: str = Field(max_length=2048)
    title: str = Field(min_length=1, max_length=512)


class SearchResult(SearchModel):
    status: Literal["results", "no_results", "blocked", "unavailable"]
    platform: Literal["taobao", "jd"]
    query: str = Field(min_length=2, max_length=120)
    search_url: str = Field(max_length=2048)
    observed_at: str
    candidates: list[SearchCandidate] = Field(default_factory=list, max_length=5)
    error_code: str | None = None
    note: str = Field(max_length=800)


class AdoptSearchCandidateArgs(SearchModel):
    search_id: str = Field(pattern=r"^search-[a-f0-9]{64}$")
    candidate_id: str = Field(pattern=r"^candidate-[a-f0-9]{16}$")
    reason: str = Field(min_length=2, max_length=500)


class ConditionCheck(SearchModel):
    condition_id: str = Field(pattern=r"^c[1-9][0-9]?$", max_length=3)
    result: Literal["matched", "unmatched", "unknown"]
    explanation: str = Field(min_length=1, max_length=500)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)


class AlternativeAssessment(SearchModel):
    item_id: str = Field(min_length=1, max_length=36)
    status: Literal["qualified", "rejected", "needs_evidence"]
    brand_relation: Literal["different", "same", "unknown"]
    brand_evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    checks: list[ConditionCheck] = Field(min_length=1, max_length=10)
    tradeoffs: str = Field(min_length=1, max_length=1200)
