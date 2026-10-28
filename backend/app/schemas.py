from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


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
