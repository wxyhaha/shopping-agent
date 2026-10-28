"""Durable, bounded jobs. Browser pages can only claim matching product identities."""

import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select, update

from app.capture_urls import product_url_identity
from app.models import AgentRun, BrowserCaptureJob, ProductItem, utc_now

JOB_TTL_SECONDS = 75
LEASE_SECONDS = 30
MAX_CLAIMS = 2
MAX_JOBS_PER_RUN = 3
TERMINAL = {"completed", "failed", "expired", "cancelled"}


class BrowserPresenceMonitor:
    """Short-lived connection observations, without persisting browsing history."""

    def __init__(self, clock=time.monotonic):
        self.lock = threading.Lock()
        self.clock = clock
        self.pages: dict[tuple[str, str, str], dict] = {}

    def observe(self, identity, browser: str, version: str | None) -> None:
        with self.lock:
            now = self.clock()
            self.pages = {
                key: value for key, value in self.pages.items() if now - value["seen"] <= 90
            }
            self.pages[(identity.platform, identity.product_id, browser)] = {
                "seen": now,
                "last_seen_at": utc_now(),
                "browser": browser,
                "extension_version": version,
            }
            while len(self.pages) > 128:
                del self.pages[min(self.pages, key=lambda key: self.pages[key]["seen"])]

    def lookup(self, identity) -> dict:
        if not identity:
            return {"connected": False}
        with self.lock:
            matches = [
                value
                for key, value in self.pages.items()
                if key[:2] == (identity.platform, identity.product_id)
                and self.clock() - value["seen"] <= 90
            ]
            if not matches:
                return {"connected": False}
            value = max(matches, key=lambda row: row["seen"])
            return {
                "connected": True,
                **{
                    key: value[key]
                    for key in (
                        "browser",
                        "extension_version",
                        "last_seen_at",
                    )
                },
            }


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def create_capture_job(session, run_id: str, item_id: str, tool_call_id: str, reason: str):
    existing = session.scalar(
        select(BrowserCaptureJob).where(
            BrowserCaptureJob.run_id == run_id,
            BrowserCaptureJob.tool_call_id == tool_call_id,
        )
    )
    if existing:
        return existing, None
    run = session.get(AgentRun, run_id)
    item = session.get(ProductItem, item_id)
    if not run or run.status != "running":
        return None, "研究已停止，不能发起新的页面采集。"
    if not item or item.task_id != run.task_id:
        return None, "商品不属于此任务。"
    if not product_url_identity(item.url):
        return None, "此地址不是支持的商品详情页。"
    count = session.scalar(
        select(func.count())
        .select_from(BrowserCaptureJob)
        .where(
            BrowserCaptureJob.run_id == run_id,
        )
    )
    if count >= MAX_JOBS_PER_RUN:
        return None, "已达到本次研究三次浏览器补采集上限，请据现有证据继续或请求用户确认。"
    job = BrowserCaptureJob(
        id=str(uuid4()),
        run_id=run_id,
        item_id=item_id,
        tool_call_id=tool_call_id,
        reason=reason,
        expires_at=utc_now() + timedelta(seconds=JOB_TTL_SECONDS),
    )
    session.add(job)
    session.commit()
    return job, None


def cancel_capture_jobs(session, run_id: str) -> None:
    session.execute(
        update(BrowserCaptureJob)
        .where(
            BrowserCaptureJob.run_id == run_id,
            BrowserCaptureJob.status.in_(["queued", "claimed"]),
        )
        .values(status="cancelled")
    )


def expire_capture_jobs(session) -> None:
    session.execute(
        update(BrowserCaptureJob)
        .where(
            BrowserCaptureJob.status.in_(["queued", "claimed"]),
            BrowserCaptureJob.expires_at <= utc_now(),
        )
        .values(status="expired", error_code="extension_timeout")
    )
