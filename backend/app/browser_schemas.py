from datetime import datetime
from typing import Literal

from pydantic import Field

from app.agent_schemas import StrictModel
from app.schemas import CaptureSnapshotSubmit


class BrowserClaimRequest(StrictModel):
    page_url: str = Field(min_length=8, max_length=8192)
    client_id: str = Field(pattern=r"^[a-fA-F0-9-]{36}$")
    browser: Literal["chrome", "edge"]
    extension_version: str | None = Field(default=None, min_length=1, max_length=32)


class BrowserPageConnection(StrictModel):
    item_id: str
    page_url: str | None
    connected: bool
    browser: Literal["chrome", "edge"] | None = None
    extension_version: str | None = None
    last_seen_at: datetime | None = None


class BrowserCaptureLease(StrictModel):
    id: str
    task_id: str
    item_id: str
    reason: str
    page_url: str
    lease_token: str
    expires_at: datetime


class BrowserClaimResponse(StrictModel):
    job: BrowserCaptureLease | None


class BrowserCaptureComplete(StrictModel):
    client_id: str = Field(pattern=r"^[a-fA-F0-9-]{36}$")
    capture: CaptureSnapshotSubmit


class BrowserCaptureFailure(StrictModel):
    client_id: str = Field(pattern=r"^[a-fA-F0-9-]{36}$")
    error_code: Literal["page_changed", "capture_failed"]


class BrowserCaptureResult(StrictModel):
    id: str
    status: str
    snapshot_id: str | None = None
