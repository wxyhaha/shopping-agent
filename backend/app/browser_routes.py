import hashlib
import secrets
import threading
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response
from sqlalchemy import and_, or_, select, update

from app.background_collector import BrowserNetworkPolicy, public_addresses
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
from app.config import get_settings
from app.models import AgentRun, BrowserCaptureJob, CaptureSnapshot, ProductItem, Task, utc_now
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
    capture = payload.capture
    settings = get_settings()
    # Detail-image reading must not run before the lease is acknowledged: reading ~30 images
    # takes about a minute, while the extension gives up after 20 seconds. Doing it inline
    # made every capture report a timeout even though the page had been read successfully.
    # Enrichment therefore runs in the background, after this response is returned.
    enrich = settings.detail_vision_enabled and bool(capture.images)
    snapshot = persist_capture_snapshot(
        run.task_id,
        job.item_id,
        capture,
        response,
        session,
        idempotency_key=f"browser-capture:{job.id}",
        commit=False,
    )
    # Mark the snapshot as pending before the thread starts, so a reader that arrives during
    # the read waits for the complete evidence instead of seeing a half-filled snapshot.
    # persist_capture_snapshot returns a read model, so the ORM row is updated directly.
    if enrich:
        record = session.get(CaptureSnapshot, snapshot.id)
        if record is not None:
            record.detail_enrichment = "pending"
    if job.status != "completed":
        session.refresh(run)
        if job.status != "claimed" or run.status != "running":
            raise HTTPException(409, "研究已停止，页面结果不再用于继续执行。")
        job.status, job.snapshot_id = "completed", snapshot.id
    session.commit()
    if enrich:
        snapshot_id = snapshot.id
        page_url = capture.page_url
        threading.Thread(
            target=_enrich_snapshot_in_background,
            args=(snapshot_id, page_url, settings),
            daemon=True,
            name="detail-image-enrichment",
        ).start()
    response.status_code = 200
    response.headers["Cache-Control"] = "no-store"
    return BrowserCaptureResult(id=job.id, status="completed", snapshot_id=job.snapshot_id)


def _enrich_snapshot_in_background(snapshot_id: str, page_url: str, settings) -> None:
    """Read detail artwork for a stored snapshot without blocking the extension's reply.

    The snapshot is already saved, so a failure here only means the detail text is missing;
    the DOM-derived fields remain valid either way.
    """
    from app.database import create_session_factory, make_engine
    from app.detail_enrich import enrich_detail_images
    from app.models import CaptureSnapshot

    engine = make_engine(settings.database_url)
    try:
        factory = create_session_factory(engine)
        with factory() as session:
            record = session.get(CaptureSnapshot, snapshot_id)
            if record is None:
                return
            try:
                identity = product_url_identity(page_url)
                policy = BrowserNetworkPolicy(
                    public_addresses, trust_fake_ip_dns=settings.browser_trust_fake_ip_dns
                )
                enriched = enrich_detail_images(
                    record.payload,
                    settings,
                    identity.platform if identity else "tmall",
                    policy,
                )
                record.payload = enriched
            except Exception:
                # The DOM-derived fields are still valid; only the detail text is missing.
                pass
            # Always settle the flag, so a waiting reader is never blocked by a failed read.
            record.detail_enrichment = "done"
            session.commit()
    finally:
        engine.dispose()


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
        job.error_detail = payload.detail
        session.commit()
    response.headers["Cache-Control"] = "no-store"
    return BrowserCaptureResult(id=job.id, status=job.status, snapshot_id=job.snapshot_id)
