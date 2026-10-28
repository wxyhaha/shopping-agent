import hashlib
import json
import re
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database import get_session
from app.models import ProductItem, Task, utc_now
from app.schemas import (
    AddItemRequest,
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
    response: Response,
    session: SessionDependency,
) -> Response:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="比较任务不存在。")
    session.delete(task)
    session.commit()
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
