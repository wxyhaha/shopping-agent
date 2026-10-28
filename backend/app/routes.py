import hashlib
import json
import re
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.capture_urls import product_url_identity, safe_product_image_url
from app.database import get_session
from app.models import AgentRun, CaptureSnapshot, ProductItem, Task, utc_now
from app.schemas import (
    AddItemRequest,
    CaptureSnapshotRead,
    CaptureSnapshotSubmit,
    CreateTaskRequest,
    ItemInput,
    ItemRead,
    TaskRead,
    TaskSummary,
    UpdateItemTitleRequest,
    UpdateTaskTitleRequest,
)
from app.title_generation import generate_task_title

router = APIRouter(prefix="/api/v1")
SessionDependency = Annotated[Session, Depends(get_session)]
IdempotencyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


def canonical_payload(payload: BaseModel) -> str:
    data = payload.model_dump(mode="json", exclude_none=False)
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def validate_idempotency_key(value: str | None) -> str:
    if value is None or re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", value) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请求缺少有效的 Idempotency-Key。",
        )
    return value


def item_to_read(item: ProductItem) -> ItemRead:
    return ItemRead(
        id=item.id,
        url=item.url,
        page_title=item.page_title,
        source=item.source,
        browser=item.browser,
        capture_status=item.capture_status,
        created_at=item.created_at,
    )


def capture_to_read(capture: CaptureSnapshot) -> CaptureSnapshotRead:
    return CaptureSnapshotRead(
        **capture.payload,
        id=capture.id,
        item_id=capture.item_id,
        platform=capture.platform,
        received_at=capture.received_at,
    )


def task_to_read(task: Task) -> TaskRead:
    return TaskRead(
        id=task.id,
        title=task.title,
        status=task.status,
        item_count=len(task.items),
        created_at=task.created_at,
        updated_at=task.updated_at,
        items=[item_to_read(item) for item in task.items],
    )


def item_to_model(item: ItemInput, key: str, payload_hash: str) -> ProductItem:
    return ProductItem(
        url=item.url,
        page_title=item.page_title or None,
        source=item.source,
        browser=item.browser,
        capture_status="not_started",
        idempotency_key=key,
        request_hash=payload_hash,
    )


def replay_or_conflict(
    session: Session,
    *,
    key: str,
    request_hash: str,
    item_request: bool,
) -> TaskRead | ItemRead:
    if item_request:
        existing_item = session.scalar(
            select(ProductItem).where(ProductItem.idempotency_key == key)
        )
        if existing_item:
            if existing_item.request_hash != request_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="幂等键已用于不同内容，请重新提交。",
                )
            return item_to_read(existing_item)
    else:
        existing_task = session.scalar(
            select(Task).options(selectinload(Task.items)).where(Task.create_idempotency_key == key)
        )
        if existing_task:
            if existing_task.create_request_hash != request_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="幂等键已用于不同内容，请重新提交。",
                )
            return task_to_read(existing_task)
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="请求发生并发冲突，请刷新任务后重试。",
    )


def normalized_capture_payload(
    payload: CaptureSnapshotSubmit,
    *,
    page_url: str,
    canonical_url: str | None,
) -> dict[str, object]:
    value = payload.model_dump(mode="json")
    # Preserve legacy schema-v1 idempotency hashes when optional variant data is absent.
    for key in ("variant_catalog", "variant_verification"):
        if value.get(key) is None:
            value.pop(key, None)
    value["page_url"] = page_url
    value["canonical_url"] = canonical_url
    return value


@router.get("/tasks", response_model=list[TaskSummary], tags=["tasks"])
def list_tasks(
    response: Response,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[TaskSummary]:
    response.headers["Cache-Control"] = "no-store"
    rows: Sequence[tuple[Task, int]] = session.execute(
        select(Task, func.count(ProductItem.id))
        .outerjoin(ProductItem)
        .group_by(Task.id)
        .order_by(Task.updated_at.desc())
        .limit(limit)
    ).all()
    return [
        TaskSummary(
            id=task.id,
            title=task.title,
            status=task.status,
            item_count=item_count,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
        for task, item_count in rows
    ]


@router.get("/tasks/{task_id}", response_model=TaskRead, tags=["tasks"])
def get_task(
    task_id: str,
    response: Response,
    session: SessionDependency,
) -> TaskRead:
    task = session.scalar(select(Task).options(selectinload(Task.items)).where(Task.id == task_id))
    if task is None:
        raise HTTPException(status_code=404, detail="比较任务不存在。")
    response.headers["Cache-Control"] = "no-store"
    return task_to_read(task)


@router.delete(
    "/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    tags=["tasks"],
)
def delete_task(
    task_id: str,
    request: Request,
    response: Response,
    session: SessionDependency,
) -> Response:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="比较任务不存在。")
    run_ids = list(session.scalars(select(AgentRun.id).where(AgentRun.task_id == task_id)))
    session.delete(task)
    session.commit()
    runtime = request.app.state.research_runtime
    runtime.delete_checkpoints([run_id for run_id in run_ids if not runtime.is_active(run_id)])
    response.headers["Cache-Control"] = "no-store"
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.patch("/tasks/{task_id}", response_model=TaskRead, tags=["tasks"])
def update_task_title(
    task_id: str,
    payload: UpdateTaskTitleRequest,
    response: Response,
    session: SessionDependency,
) -> TaskRead:
    task = session.scalar(select(Task).options(selectinload(Task.items)).where(Task.id == task_id))
    if task is None:
        raise HTTPException(status_code=404, detail="比较任务不存在。")
    task.title = payload.title
    task.updated_at = utc_now()
    session.commit()
    session.refresh(task)
    response.headers["Cache-Control"] = "no-store"
    return task_to_read(task)


@router.post(
    "/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    tags=["tasks"],
)
async def create_task(
    payload: CreateTaskRequest,
    response: Response,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> TaskRead:
    key = validate_idempotency_key(idempotency_key)
    request_hash = canonical_payload(payload)
    existing_task = session.scalar(
        select(Task).options(selectinload(Task.items)).where(Task.create_idempotency_key == key)
    )
    if existing_task is not None:
        if existing_task.create_request_hash != request_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="幂等键已用于不同内容，请重新提交。",
            )
        response.headers["Cache-Control"] = "no-store"
        return task_to_read(existing_task)

    title = payload.title or await generate_task_title([item.page_title for item in payload.items])
    now = utc_now()
    task = Task(
        title=title,
        status="open",
        create_idempotency_key=key,
        create_request_hash=request_hash,
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    for index, item in enumerate(payload.items):
        item_key = f"task-create:{hashlib.sha256(key.encode('utf-8')).hexdigest()}:{index}"
        task.items.append(
            item_to_model(
                item,
                item_key,
                canonical_payload(item),
            )
        )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return replay_or_conflict(
            session,
            key=key,
            request_hash=request_hash,
            item_request=False,
        )
    session.refresh(task)
    response.headers["Cache-Control"] = "no-store"
    return task_to_read(task)


@router.post(
    "/tasks/{task_id}/items",
    response_model=ItemRead,
    status_code=status.HTTP_201_CREATED,
    tags=["items"],
)
def add_item(
    task_id: str,
    payload: AddItemRequest,
    response: Response,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> ItemRead:
    key = validate_idempotency_key(idempotency_key)
    task = session.scalar(select(Task).where(Task.id == task_id))
    if task is None:
        raise HTTPException(status_code=404, detail="比较任务不存在。")

    request_hash = hashlib.sha256(f"{task_id}:{canonical_payload(payload)}".encode()).hexdigest()
    item = item_to_model(payload, key, request_hash)
    task.items.append(item)
    task.updated_at = utc_now()
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        result = replay_or_conflict(
            session,
            key=key,
            request_hash=request_hash,
            item_request=True,
        )
        if not isinstance(result, ItemRead):
            raise HTTPException(status_code=409, detail="请求发生冲突。") from None
        response.headers["Cache-Control"] = "no-store"
        return result
    session.refresh(item)
    response.headers["Cache-Control"] = "no-store"
    return item_to_read(item)


@router.post(
    "/tasks/{task_id}/items/{item_id}/captures",
    response_model=CaptureSnapshotRead,
    status_code=status.HTTP_201_CREATED,
    tags=["captures"],
)
def create_capture_snapshot(
    task_id: str,
    item_id: str,
    payload: CaptureSnapshotSubmit,
    response: Response,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> CaptureSnapshotRead:
    return persist_capture_snapshot(task_id, item_id, payload, response, session, idempotency_key)


def persist_capture_snapshot(
    task_id: str,
    item_id: str,
    payload: CaptureSnapshotSubmit,
    response: Response,
    session: Session,
    idempotency_key: str | None,
    *,
    commit: bool = True,
) -> CaptureSnapshotRead:
    key = validate_idempotency_key(idempotency_key)
    item = session.scalar(
        select(ProductItem).where(ProductItem.id == item_id, ProductItem.task_id == task_id)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="商品链接不存在。")

    item_identity = product_url_identity(item.url)
    page_identity = product_url_identity(payload.page_url)
    if (
        item_identity is None
        or page_identity is None
        or item_identity.platform != page_identity.platform
        or item_identity.product_id != page_identity.product_id
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="采集页面必须是已保存的淘宝、天猫或京东同一商品。",
        )
    if not payload.adapter_id.startswith(f"{item_identity.platform}-"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="采集适配器与商品平台不匹配。",
        )

    canonical_identity = (
        product_url_identity(payload.canonical_url) if payload.canonical_url else None
    )
    if payload.canonical_url and (
        canonical_identity is None
        or canonical_identity.platform != item_identity.platform
        or canonical_identity.product_id != item_identity.product_id
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="规范链接必须指向已保存的同一商品。",
        )

    stored_payload = normalized_capture_payload(
        payload,
        page_url=page_identity.safe_url,
        canonical_url=canonical_identity.safe_url if canonical_identity else None,
    )
    for image in stored_payload["images"]:
        source_url = image.get("source_url")
        if source_url:
            safe_url = safe_product_image_url(source_url)
            if safe_url is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="商品图片来源必须来自支持平台的公开图片 CDN。",
                )
            image["source_url"] = safe_url
    serialized = json.dumps(
        stored_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(serialized.encode("utf-8")) > 512 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="采集快照超过 512 KiB 上限。",
        )
    request_hash = hashlib.sha256(f"{task_id}:{item_id}:{serialized}".encode()).hexdigest()

    existing = session.scalar(select(CaptureSnapshot).where(CaptureSnapshot.idempotency_key == key))
    if existing is not None:
        if existing.request_hash != request_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="幂等键已用于不同采集数据，请重新提交。",
            )
        response.status_code = status.HTTP_200_OK
        response.headers["Cache-Control"] = "no-store"
        return capture_to_read(existing)

    received_at = utc_now()
    snapshot = CaptureSnapshot(
        item_id=item_id,
        platform=item_identity.platform,
        adapter_id=payload.adapter_id,
        adapter_version=payload.adapter_version,
        status=payload.status,
        captured_at=payload.captured_at,
        received_at=received_at,
        page_url=page_identity.safe_url,
        canonical_url=canonical_identity.safe_url if canonical_identity else None,
        schema_version=payload.schema_version,
        payload=stored_payload,
        idempotency_key=key,
        request_hash=request_hash,
    )
    item.capture_status = payload.status
    task = session.get(Task, task_id)
    if task is not None:
        task.updated_at = received_at
    session.add(snapshot)
    try:
        if commit:
            session.commit()
        else:
            session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(CaptureSnapshot).where(CaptureSnapshot.idempotency_key == key)
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="采集快照写入发生并发冲突，请刷新后重试。",
            ) from None
        if existing.request_hash != request_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="幂等键已用于不同采集数据，请重新提交。",
            ) from None
        response.status_code = status.HTTP_200_OK
        response.headers["Cache-Control"] = "no-store"
        return capture_to_read(existing)

    session.refresh(snapshot)
    response.headers["Cache-Control"] = "no-store"
    return capture_to_read(snapshot)


@router.get(
    "/tasks/{task_id}/items/{item_id}/captures",
    response_model=list[CaptureSnapshotRead],
    tags=["captures"],
)
def list_capture_snapshots(
    task_id: str,
    item_id: str,
    response: Response,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> list[CaptureSnapshotRead]:
    item_exists = session.scalar(
        select(ProductItem.id).where(ProductItem.id == item_id, ProductItem.task_id == task_id)
    )
    if item_exists is None:
        raise HTTPException(status_code=404, detail="商品链接不存在。")
    captures = session.scalars(
        select(CaptureSnapshot)
        .where(CaptureSnapshot.item_id == item_id)
        .order_by(CaptureSnapshot.received_at.desc(), CaptureSnapshot.id.desc())
        .limit(limit)
    ).all()
    response.headers["Cache-Control"] = "no-store"
    return [capture_to_read(capture) for capture in captures]


@router.patch(
    "/tasks/{task_id}/items/{item_id}",
    response_model=ItemRead,
    tags=["items"],
)
def update_item_title(
    task_id: str,
    item_id: str,
    payload: UpdateItemTitleRequest,
    response: Response,
    session: SessionDependency,
) -> ItemRead:
    task = session.scalar(select(Task).where(Task.id == task_id))
    if task is None:
        raise HTTPException(status_code=404, detail="比较任务不存在。")
    item = session.scalar(
        select(ProductItem).where(ProductItem.id == item_id, ProductItem.task_id == task_id)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="商品链接不存在。")
    item.page_title = payload.page_title
    task.updated_at = utc_now()
    session.commit()
    session.refresh(item)
    response.headers["Cache-Control"] = "no-store"
    return item_to_read(item)


@router.delete(
    "/tasks/{task_id}/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    tags=["items"],
)
def delete_item(
    task_id: str,
    item_id: str,
    response: Response,
    session: SessionDependency,
) -> Response:
    task = session.scalar(select(Task).where(Task.id == task_id))
    if task is None:
        raise HTTPException(status_code=404, detail="比较任务不存在。")
    item = session.scalar(
        select(ProductItem).where(ProductItem.id == item_id, ProductItem.task_id == task_id)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="商品链接不存在。")
    session.delete(item)
    task.updated_at = utc_now()
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
