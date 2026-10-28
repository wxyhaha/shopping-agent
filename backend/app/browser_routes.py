import hashlib
import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response
from sqlalchemy import and_, or_, select, update

from app.browser_capture import LEASE_SECONDS, MAX_CLAIMS, aware, expire_capture_jobs
from app.browser_schemas import (
    BrowserCaptureComplete,
    BrowserCaptureFailure,
    BrowserCaptureLease,
    BrowserCaptureResult,
    BrowserClaimRequest,
    BrowserClaimResponse,
    BrowserPageConnection,
)
from app.capture_urls import product_url_identity
from app.models import AgentRun, BrowserCaptureJob, ProductItem, Task, utc_now
from app.routes import SessionDependency, persist_capture_snapshot

router = APIRouter(prefix="/api/v1/browser-captures", tags=["browser-tools"])
LeaseHeader = Annotated[str, Header(alias="X-Capture-Lease", min_length=32, max_length=128)]


@router.post("/claim", response_model=BrowserClaimResponse)
def claim_capture(
    payload: BrowserClaimRequest, request: Request, response: Response, session: SessionDependency
):
    identity = product_url_identity(payload.page_url)
    if not identity:
        raise HTTPException(422, "只能从支持的商品详情页领取采集请求。")
    request.app.state.browser_presence.observe(identity, payload.browser, payload.extension_version)
    now = utc_now()
    expire_capture_jobs(session)
    session.commit()
    candidates = session.execute(
        select(BrowserCaptureJob, ProductItem, AgentRun)
        .join(
            ProductItem,
            BrowserCaptureJob.item_id == ProductItem.id,
        )
        .join(AgentRun, BrowserCaptureJob.run_id == AgentRun.id)
        .where(
            AgentRun.status == "running",
            BrowserCaptureJob.expires_at > now,
            BrowserCaptureJob.attempts < MAX_CLAIMS,
            or_(
                BrowserCaptureJob.status == "queued",
                and_(
                    BrowserCaptureJob.status == "claimed",
                    BrowserCaptureJob.lease_expires_at <= now,
                ),
            ),
        )
        .order_by(BrowserCaptureJob.created_at)
        .limit(50)
    ).all()
    response.headers["Cache-Control"] = "no-store"
    for job, item, run in candidates:
        target = product_url_identity(item.url)
        if not target or (identity.platform, identity.product_id) != (
            target.platform,
            target.product_id,
        ):
            continue
        token = secrets.token_urlsafe(32)
        lease_expires = min(aware(job.expires_at), now + timedelta(seconds=LEASE_SECONDS))
        changed = session.execute(
            update(BrowserCaptureJob)
            .where(
                BrowserCaptureJob.id == job.id,
                BrowserCaptureJob.attempts == job.attempts,
                BrowserCaptureJob.status == job.status,
            )
            .values(
                status="claimed",
                client_id=payload.client_id,
                browser=payload.browser,
                attempts=job.attempts + 1,
                lease_expires_at=lease_expires,
                lease_hash=hashlib.sha256(token.encode()).hexdigest(),
            )
        )
        if changed.rowcount != 1:
            session.rollback()
            continue
        session.commit()
        return BrowserClaimResponse(
            job=BrowserCaptureLease(
                id=job.id,
                task_id=run.task_id,
                item_id=item.id,
                page_url=target.safe_url,
                reason=job.reason,
                lease_token=token,
                expires_at=lease_expires,
            )
        )
    return BrowserClaimResponse(job=None)


@router.get("/status/{task_id}", response_model=list[BrowserPageConnection])
def browser_connection_status(
    task_id: str, request: Request, response: Response, session: SessionDependency
):
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "比较任务不存在。")
    response.headers["Cache-Control"] = "no-store"
    result = []
    for item in task.items:
        identity = product_url_identity(item.url)
        result.append(
            BrowserPageConnection(
                item_id=item.id,
                page_url=identity.safe_url if identity else None,
                **request.app.state.browser_presence.lookup(identity),
            )
        )
    return result


def owned_job(session, job_id: str, token: str, client_id: str) -> BrowserCaptureJob:
    job = session.get(BrowserCaptureJob, job_id)
    if not job:
        raise HTTPException(404, "采集请求不存在。")
    digest = hashlib.sha256(token.encode()).hexdigest()
    if job.client_id != client_id or not secrets.compare_digest(job.lease_hash or "", digest):
        raise HTTPException(403, "采集请求的执行凭据无效。")
    if job.status == "completed":
        return job
    run = session.get(AgentRun, job.run_id)
    if not run or run.status != "running" or job.status != "claimed":
        raise HTTPException(409, "研究已停止或采集请求不再有效。")
    if (
        not job.lease_expires_at
        or aware(job.lease_expires_at) <= utc_now()
        or aware(job.expires_at) <= utc_now()
    ):
        raise HTTPException(409, "采集执行凭据已过期。")
    return job


@router.post("/{job_id}/complete", response_model=BrowserCaptureResult)
def complete_capture(
    job_id: str,
    payload: BrowserCaptureComplete,
    response: Response,
    session: SessionDependency,
    lease_token: LeaseHeader,
):
    job = owned_job(session, job_id, lease_token, payload.client_id)
    run = session.get(AgentRun, job.run_id)
    if not run or not job.item_id:
        raise HTTPException(409, "商品或研究已删除。")
    if aware(payload.capture.captured_at) < aware(job.created_at) - timedelta(seconds=5):
        raise HTTPException(422, "必须在收到请求后重新读取页面，不能回传旧快照。")
    # Reuse all capture identity, schema, evidence, URL and size validation. The job ID is
    # a stable idempotency key, so a lost completion response cannot duplicate the snapshot.
    snapshot = persist_capture_snapshot(
        run.task_id,
        job.item_id,
        payload.capture,
        response,
        session,
        idempotency_key=f"browser-capture:{job.id}",
        commit=False,
    )
    if job.status != "completed":
        session.refresh(run)
        if job.status != "claimed" or run.status != "running":
            raise HTTPException(409, "研究已停止，页面结果不再用于继续执行。")
        job.status, job.snapshot_id = "completed", snapshot.id
    session.commit()
    response.status_code = 200
    response.headers["Cache-Control"] = "no-store"
    return BrowserCaptureResult(id=job.id, status="completed", snapshot_id=job.snapshot_id)


@router.post("/{job_id}/fail", response_model=BrowserCaptureResult)
def fail_capture(
    job_id: str,
    payload: BrowserCaptureFailure,
    response: Response,
    session: SessionDependency,
    lease_token: LeaseHeader,
):
    job = owned_job(session, job_id, lease_token, payload.client_id)
    if job.status != "completed":
        job.status, job.error_code = "failed", payload.error_code
        session.commit()
    response.headers["Cache-Control"] = "no-store"
    return BrowserCaptureResult(id=job.id, status=job.status, snapshot_id=job.snapshot_id)
