from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VariantChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: str = Field(pattern=r"^g-[a-f0-9]{1,8}$")
    option_id: str = Field(pattern=r"^o-[a-f0-9]{1,8}$")


class VariantOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^o-[a-f0-9]{1,8}$")
    label: str = Field(min_length=1, max_length=512)
    selected: bool
    disabled: bool
    selectable: bool
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class VariantGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^g-[a-f0-9]{1,8}$")
    label: str = Field(min_length=1, max_length=128)
    options: list[VariantOption] = Field(min_length=1, max_length=40)


class VariantCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ready", "missing", "ambiguous"]
    groups: list[VariantGroup] = Field(default_factory=list, max_length=12)
    truncated: bool = False

    @model_validator(mode="after")
    def bounded(self):
        if sum(len(group.options) for group in self.groups) > 160:
            raise ValueError("规格选项超过上限。")
        if self.status == "ready":
            if not self.groups or len({g.id for g in self.groups}) != len(self.groups):
                raise ValueError("可操作规格分组必须存在且唯一。")
            for group in self.groups:
                if len({o.id for o in group.options}) != len(group.options):
                    raise ValueError("可操作规格选项必须唯一。")
        return self


class VariantVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal[
        "selected", "stale_catalog", "unavailable", "not_selected", "page_changed", "unstable"
    ]
    requested: list[VariantChoice] = Field(min_length=1, max_length=12)
    price_binding: Literal["same_page_state", "unknown"] = "unknown"
    note: str = Field(min_length=1, max_length=512)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
