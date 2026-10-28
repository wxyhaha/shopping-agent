from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.variant_schemas import VariantCatalog, VariantVerification


class ItemInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    url: str = Field(min_length=8, max_length=8192)
    page_title: str | None = Field(default=None, max_length=512)
    source: Literal["web", "extension"] = "web"
    browser: Literal["chrome", "edge"] | None = None

    @field_validator("url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        try:
            parts = urlsplit(value)
            hostname = parts.hostname
            _ = parts.port
        except ValueError as exc:
            raise ValueError("请输入有效的商品链接。") from exc
        if parts.scheme.lower() not in {"http", "https"} or not hostname:
            raise ValueError("商品链接必须以 http:// 或 https:// 开头。")
        if parts.username or parts.password:
            raise ValueError("链接中不能包含用户名或密码。")
        return value


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=120)
    items: list[ItemInput] = Field(min_length=1, max_length=20)


class UpdateTaskTitleRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1, max_length=120)


class UpdateItemTitleRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    page_title: str = Field(min_length=1, max_length=512)


class ItemRead(BaseModel):
    id: str
    url: str
    page_title: str | None
    source: str
    browser: str | None
    capture_status: str
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class TaskSummary(BaseModel):
    id: str
    title: str
    status: str
    item_count: int
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_timestamp(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class TaskRead(TaskSummary):
    items: list[ItemRead]


class AddItemRequest(ItemInput):
    pass


class ErrorRead(BaseModel):
    detail: str


CaptureFieldStatus = Literal[
    "observed",
    "user_confirmed",
    "ambiguous",
    "missing",
    "blocked",
    "not_applicable",
]
CaptureResultStatus = Literal["complete", "partial", "blocked", "failed"]
EvidenceSource = Literal["visible_dom", "json_ld", "user_confirmed", "image_metadata", "navigation"]
PriceKind = Literal[
    "current",
    "list",
    "member",
    "coupon",
    "deposit",
    "installment",
    "unit",
    "shipping",
    "unknown",
]

_CAPTURE_ID_PATTERN = r"^[A-Za-z0-9._:-]{1,64}$"
_FIELD_PATH_PATTERN = r"^[A-Za-z][A-Za-z0-9_.\[\]-]{0,159}$"


class CaptureEvidenceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    evidence_id: str = Field(pattern=_CAPTURE_ID_PATTERN)
    field_path: str = Field(pattern=_FIELD_PATH_PATTERN)
    source: EvidenceSource
    excerpt: str | None = Field(default=None, max_length=2048)
    locator: str | None = Field(default=None, max_length=256)


class CaptureObservationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    observation_id: str = Field(pattern=_CAPTURE_ID_PATTERN)
    field_path: str = Field(pattern=_FIELD_PATH_PATTERN)
    label: str | None = Field(default=None, max_length=128)
    raw_value: str | None = Field(default=None, max_length=2048)
    normalized_value: str | None = Field(default=None, max_length=2048)
    unit: str | None = Field(default=None, max_length=64)
    status: CaptureFieldStatus
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_observation(self) -> "CaptureObservationInput":
        if self.status in {"observed", "user_confirmed"} and not self.raw_value:
            raise ValueError("已观察或已确认的字段必须保留原始值。")
        if self.status in {"observed", "user_confirmed"} and not self.evidence_ids:
            raise ValueError("已观察或已确认的字段必须关联证据。")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("同一字段不能重复引用相同证据。")
        return self


class PriceConditionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    condition: Literal[
        "quantity",
        "region",
        "shipping",
        "member",
        "coupon",
        "threshold",
        "installment",
        "time_window",
        "other",
    ]
    raw_value: str | None = Field(default=None, max_length=512)
    normalized_value: str | None = Field(default=None, max_length=512)
    status: CaptureFieldStatus
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_condition(self) -> "PriceConditionInput":
        if self.status in {"observed", "user_confirmed"} and not self.raw_value:
            raise ValueError("已观察或已确认的价格条件必须保留原始值。")
        if self.status in {"observed", "user_confirmed"} and not self.evidence_ids:
            raise ValueError("已观察或已确认的价格条件必须关联证据。")
        return self


class PriceObservationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    observation_id: str = Field(pattern=_CAPTURE_ID_PATTERN)
    kind: PriceKind
    raw_text: str | None = Field(default=None, max_length=512)
    amount: Decimal | None = Field(default=None, ge=Decimal("0"), max_digits=18, decimal_places=4)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    status: CaptureFieldStatus
    conditions: list[PriceConditionInput] = Field(default_factory=list, max_length=20)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_price(self) -> "PriceObservationInput":
        if self.status in {"observed", "user_confirmed"} and not self.raw_text:
            raise ValueError("已观察或已确认的价格必须保留页面原文。")
        if self.status in {"observed", "user_confirmed"} and not self.evidence_ids:
            raise ValueError("已观察或已确认的价格必须关联证据。")
        return self


class VariantSelectionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    status: Literal["observed", "user_confirmed", "ambiguous", "missing", "blocked"]
    label: str | None = Field(default=None, max_length=512)
    attributes: list[CaptureObservationInput] = Field(default_factory=list, max_length=50)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_selection(self) -> "VariantSelectionInput":
        if self.status in {"observed", "user_confirmed"} and not (self.label or self.attributes):
            raise ValueError("已观察或已确认的规格必须包含标签或属性。")
        if self.status in {"observed", "user_confirmed"} and not (
            self.evidence_ids or any(attribute.evidence_ids for attribute in self.attributes)
        ):
            raise ValueError("已观察或已确认的规格必须关联证据。")
        return self


class ImageObservationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    index: int = Field(ge=0, le=500)
    source_url: str | None = Field(default=None, max_length=2048)
    alt_text: str | None = Field(default=None, max_length=512)
    loaded: bool | None = None
    width: int | None = Field(default=None, ge=0, le=20000)
    height: int | None = Field(default=None, ge=0, le=20000)
    status: Literal["observed", "ambiguous", "missing", "blocked", "not_applicable"]
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_image(self) -> "ImageObservationInput":
        if self.status == "observed" and not self.evidence_ids:
            raise ValueError("已观察的图片必须关联图片元数据证据。")
        if self.status == "observed" and (not self.loaded or not self.source_url):
            raise ValueError("已观察的图片必须已加载并保留来源地址。")
        if self.source_url is not None:
            try:
                parsed = urlsplit(self.source_url)
                _ = parsed.port
            except ValueError as exc:
                raise ValueError("商品图片来源地址无效。") from exc
            if parsed.scheme.lower() != "https" or not parsed.hostname:
                raise ValueError("商品图片来源地址必须使用 HTTPS。")
            if parsed.username or parsed.password:
                raise ValueError("商品图片来源地址不能包含用户名或密码。")
        return self


class CaptureCoverageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    field_path: str = Field(pattern=_FIELD_PATH_PATTERN)
    status: CaptureFieldStatus
    note: str | None = Field(default=None, max_length=512)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_coverage(self) -> "CaptureCoverageInput":
        if self.status in {"observed", "user_confirmed"} and not self.evidence_ids:
            raise ValueError("已观察或已确认的覆盖状态必须关联证据。")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("同一覆盖状态不能重复引用相同证据。")
        return self


class CaptureSnapshotSubmit(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    schema_version: Literal[1] = 1
    adapter_id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9-]+$")
    adapter_version: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9.+_-]+$")
    page_url: str = Field(min_length=8, max_length=8192)
    canonical_url: str | None = Field(default=None, min_length=8, max_length=8192)
    captured_at: datetime
    status: CaptureResultStatus
    fields: list[CaptureObservationInput] = Field(default_factory=list, max_length=200)
    selected_variant: VariantSelectionInput
    variant_catalog: VariantCatalog | None = None
    variant_verification: VariantVerification | None = None
    prices: list[PriceObservationInput] = Field(default_factory=list, max_length=50)
    images: list[ImageObservationInput] = Field(default_factory=list, max_length=200)
    evidence: list[CaptureEvidenceInput] = Field(default_factory=list, max_length=500)
    coverage: list[CaptureCoverageInput] = Field(default_factory=list, max_length=200)

    @field_validator("page_url", "canonical_url")
    @classmethod
    def validate_capture_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = urlsplit(value)
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("采集页面地址无效。") from exc
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("采集页面地址必须使用 HTTP 或 HTTPS。")
        if parsed.username or parsed.password:
            raise ValueError("采集页面地址不能包含用户名或密码。")
        return value

    @field_validator("captured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("采集时间必须包含时区。")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_evidence_references(self) -> "CaptureSnapshotSubmit":
        evidence_ids = [entry.evidence_id for entry in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("证据 ID 必须唯一。")
        evidence_by_id = {entry.evidence_id: entry for entry in self.evidence}
        available = set(evidence_by_id)

        observation_ids = [entry.observation_id for entry in self.fields]
        observation_ids.extend(
            attribute.observation_id for attribute in self.selected_variant.attributes
        )
        observation_ids.extend(entry.observation_id for entry in self.prices)
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("观察记录 ID 必须唯一。")

        references = list(self.selected_variant.evidence_ids)
        if self.variant_catalog:
            for group in self.variant_catalog.groups:
                for option in group.options:
                    if not option.evidence_ids:
                        raise ValueError("规格选项必须关联页面证据。")
                    references.extend(option.evidence_ids)
        if self.variant_verification:
            if not self.variant_verification.evidence_ids:
                raise ValueError("规格核验必须关联页面证据。")
            references.extend(self.variant_verification.evidence_ids)
        for entry in self.fields:
            references.extend(entry.evidence_ids)
        for attribute in self.selected_variant.attributes:
            references.extend(attribute.evidence_ids)
        for price in self.prices:
            references.extend(price.evidence_ids)
            for condition in price.conditions:
                references.extend(condition.evidence_ids)
        for image in self.images:
            if len(image.evidence_ids) != len(set(image.evidence_ids)):
                raise ValueError("同一图片不能重复引用相同证据。")
            references.extend(image.evidence_ids)
        for entry in self.coverage:
            references.extend(entry.evidence_ids)
        unknown = sorted(set(references) - available)
        if unknown:
            raise ValueError("采集字段引用了不存在的证据。")
        if available - set(references):
            raise ValueError("每条证据都必须关联到采集字段、规格、价格、图片或覆盖状态。")

        for image in self.images:
            if image.status == "observed" and not any(
                evidence_by_id[reference].source == "image_metadata"
                for reference in image.evidence_ids
            ):
                raise ValueError("已观察的图片必须引用图片元数据来源。")

        def require_user_confirmation(status: str, reference_ids: list[str]) -> None:
            if status == "user_confirmed" and not any(
                evidence_by_id[reference].source == "user_confirmed" for reference in reference_ids
            ):
                raise ValueError("用户确认状态必须引用用户确认来源。")

        for entry in self.fields:
            require_user_confirmation(entry.status, entry.evidence_ids)
        for attribute in self.selected_variant.attributes:
            require_user_confirmation(attribute.status, attribute.evidence_ids)
        for price in self.prices:
            require_user_confirmation(price.status, price.evidence_ids)
            for condition in price.conditions:
                require_user_confirmation(condition.status, condition.evidence_ids)
        for entry in self.coverage:
            require_user_confirmation(entry.status, entry.evidence_ids)
        variant_evidence = list(self.selected_variant.evidence_ids)
        for attribute in self.selected_variant.attributes:
            variant_evidence.extend(attribute.evidence_ids)
        require_user_confirmation(self.selected_variant.status, variant_evidence)

        statuses = [entry.status for entry in self.fields]
        statuses.extend(attribute.status for attribute in self.selected_variant.attributes)
        statuses.append(self.selected_variant.status)
        for price in self.prices:
            statuses.append(price.status)
            statuses.extend(condition.status for condition in price.conditions)
        statuses.extend(image.status for image in self.images)
        statuses.extend(entry.status for entry in self.coverage)
        if self.status == "complete":
            incomplete = {"ambiguous", "missing", "blocked"}
            if not self.coverage or incomplete.intersection(statuses):
                raise ValueError("完整快照必须声明覆盖范围，且不能包含歧义、缺失或阻断字段。")
        if self.status == "blocked" and "blocked" not in statuses:
            raise ValueError("阻断快照必须记录至少一个受阻字段。")

        coverage_paths = [entry.field_path for entry in self.coverage]
        if len(coverage_paths) != len(set(coverage_paths)):
            raise ValueError("同一字段的覆盖状态只能出现一次。")
        image_indexes = [entry.index for entry in self.images]
        if len(image_indexes) != len(set(image_indexes)):
            raise ValueError("详情图片序号必须唯一。")
        return self

    @field_serializer("captured_at")
    def serialize_captured_at(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class CaptureSnapshotRead(CaptureSnapshotSubmit):
    id: str
    item_id: str
    platform: Literal["taobao", "tmall", "jd"]
    received_at: datetime

    @field_serializer("received_at")
    def serialize_received_at(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
