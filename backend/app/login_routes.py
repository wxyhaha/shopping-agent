from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.browser_login import BrowserLoginError
from app.models import ProductItem
from app.routes import SessionDependency

router = APIRouter(prefix="/api/v1/tasks/{task_id}/items/{item_id}/browser-login", tags=["login"])


class BrowserLoginRead(BaseModel):
    id: str
    task_id: str
    item_id: str
    platform: Literal["taobao", "jd"]
    status: Literal["opening", "awaiting_user", "saving", "saved", "failed", "cancelled", "expired"]
    error: str | None
    # Set when the window landed on a platform interstitial instead of a normal page, so
    # the workbench can say so rather than leaving the user staring at an unusable window.
    entry_note: str | None = None


def check_item(session, task_id, item_id):
    item = session.get(ProductItem, item_id)
    if not item or item.task_id != task_id:
        raise HTTPException(404, "商品不属于此任务。")
    return item


def check_user_origin(request):
    origin = request.headers.get("origin")
    allowed = {str(request.base_url).rstrip("/"), "http://127.0.0.1:5173", "http://localhost:5173"}
    if (origin and origin not in allowed) or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "请从本地工作台操作登录窗口。")


@router.get("", response_model=BrowserLoginRead | None)
def read_login(
    task_id: str, item_id: str, request: Request, response: Response, session: SessionDependency
):
    check_item(session, task_id, item_id)
    response.headers["Cache-Control"] = "no-store"
    return request.app.state.browser_login.read(task_id, item_id)


@router.post("", response_model=BrowserLoginRead, status_code=202)
def start_login(
    task_id: str, item_id: str, request: Request, response: Response, session: SessionDependency
):
    check_user_origin(request)
    item = check_item(session, task_id, item_id)
    response.headers["Cache-Control"] = "no-store"
    try:
        return request.app.state.browser_login.start(task_id, item_id, item.url)
    except BrowserLoginError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/{login_id}/{action}", response_model=BrowserLoginRead)
def control_login(
    task_id: str,
    item_id: str,
    login_id: str,
    action: Literal["save", "cancel"],
    request: Request,
    response: Response,
    session: SessionDependency,
):
    check_user_origin(request)
    check_item(session, task_id, item_id)
    response.headers["Cache-Control"] = "no-store"
    try:
        return request.app.state.browser_login.command(task_id, item_id, login_id, action)
    except BrowserLoginError as exc:
        raise HTTPException(409, str(exc)) from None
