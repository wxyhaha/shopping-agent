import hashlib
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.agent_schemas import ResearchRunRead, ResumeResearchRequest, StartResearchRequest
from app.browser_capture import cancel_capture_jobs
from app.config import get_settings
from app.models import AgentRun, Task, utc_now
from app.research_agent import AgentFailure
from app.routes import (
    IdempotencyHeader,
    SessionDependency,
    canonical_payload,
    validate_idempotency_key,
)

router = APIRouter(prefix="/api/v1", tags=["research"])


def find_run(session, task_id: str, run_id: str) -> AgentRun:
    run = session.get(AgentRun, run_id)
    if not run or run.task_id != task_id:
        raise HTTPException(404, "研究运行不存在。")
    return run


@router.post("/tasks/{task_id}/research", response_model=ResearchRunRead, status_code=202)
def start_research(
    task_id: str,
    payload: StartResearchRequest,
    request: Request,
    response: Response,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> ResearchRunRead:
    key = validate_idempotency_key(idempotency_key)
    digest = hashlib.sha256(f"{task_id}:{canonical_payload(payload)}".encode()).hexdigest()
    existing = session.scalar(select(AgentRun).where(AgentRun.idempotency_key == key))
    if existing:
        if existing.request_hash != digest:
            raise HTTPException(409, "幂等键已用于不同研究需求。")
        response.headers["Cache-Control"] = "no-store"
        return ResearchRunRead.model_validate(existing)
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "比较任务不存在。")
    if not task.items:
        raise HTTPException(422, "请先添加需要研究的商品。")
    settings = get_settings()
    if not settings.llm_base_url or not settings.llm_model:
        raise HTTPException(503, "请先配置本机模型网关和模型名称。")
    if request.app.state.research_runtime.closing:
        raise HTTPException(503, "服务正在关闭，请稍后重试。")
    run = AgentRun(
        id=str(uuid4()),
        task_id=task_id,
        goal=payload.goal,
        model=settings.llm_model,
        status="queued",
        idempotency_key=key,
        request_hash=digest,
    )
    session.add(run)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(AgentRun).where(AgentRun.idempotency_key == key))
        if existing and existing.request_hash == digest:
            return ResearchRunRead.model_validate(existing)
        raise HTTPException(409, "此任务已有未结束的研究，请继续或停止当前研究。") from None
    result = ResearchRunRead.model_validate(run)
    request.app.state.research_runtime.dispatch(run.id)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/tasks/{task_id}/research", response_model=list[ResearchRunRead])
def list_research(
    task_id: str,
    response: Response,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> list[ResearchRunRead]:
    if not session.get(Task, task_id):
        raise HTTPException(404, "比较任务不存在。")
    runs = session.scalars(
        select(AgentRun)
        .where(AgentRun.task_id == task_id)
        .order_by(
            AgentRun.created_at.desc(),
            AgentRun.id.desc(),
        )
        .limit(limit)
    )
    response.headers["Cache-Control"] = "no-store"
    return [ResearchRunRead.model_validate(run) for run in runs]


@router.post(
    "/tasks/{task_id}/research/{run_id}/resume", response_model=ResearchRunRead, status_code=202
)
def resume_research(
    task_id: str,
    run_id: str,
    payload: ResumeResearchRequest,
    request: Request,
    response: Response,
    session: SessionDependency,
) -> ResearchRunRead:
    run = find_run(session, task_id, run_id)
    if run.status not in {"needs_input", "failed", "interrupted"}:
        raise HTTPException(409, "当前运行不在可继续状态。")
    if run.question and not payload.answer:
        raise HTTPException(422, "请先回答 Agent 的确认问题。")
    runtime = request.app.state.research_runtime
    if runtime.is_active(run_id):
        raise HTTPException(409, "运行正在保存检查点，请稍后再试。")
    try:
        changed = session.execute(
            update(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.status == run.status,
            )
            .values(status="queued", error=None, updated_at=utc_now())
        )
        if changed.rowcount != 1:
            raise HTTPException(409, "运行状态已变化，请刷新。")
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "此任务有其他未结束的研究。") from None
    try:
        runtime.dispatch(run_id, payload.answer)
    except AgentFailure as exc:
        raise HTTPException(409, str(exc)) from None
    session.refresh(run)
    response.headers["Cache-Control"] = "no-store"
    return ResearchRunRead.model_validate(run)


@router.post("/tasks/{task_id}/research/{run_id}/cancel", response_model=ResearchRunRead)
def cancel_research(
    task_id: str,
    run_id: str,
    request: Request,
    response: Response,
    session: SessionDependency,
) -> ResearchRunRead:
    run = find_run(session, task_id, run_id)
    if run.status in {"completed", "cancelled"}:
        raise HTTPException(409, "此运行已经结束。")
    session.execute(
        update(AgentRun)
        .where(AgentRun.id == run_id)
        .values(
            status="cancelled",
            question=None,
            updated_at=utc_now(),
        )
    )
    cancel_capture_jobs(session, run_id)
    session.commit()
    runtime = request.app.state.research_runtime
    if not runtime.is_active(run_id):
        runtime.delete_checkpoints([run_id])
    session.refresh(run)
    response.headers["Cache-Control"] = "no-store"
    return ResearchRunRead.model_validate(run)
