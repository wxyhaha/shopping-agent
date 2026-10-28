import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import URL, Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.agent_schemas import CollectionAvailability
from app.browser_capture import BrowserPresenceMonitor
from app.browser_login import BrowserLoginManager
from app.browser_routes import router as browser_router
from app.config import PROJECT_ROOT, get_settings
from app.database import create_session_factory, make_engine
from app.login_routes import router as login_router
from app.migrations import upgrade_database
from app.research_agent import ResearchRuntime
from app.research_routes import router as research_router
from app.routes import router as api_router


def create_app(
    database_url: URL | str | None = None,
    *,
    run_migrations_on_startup: bool = True,
    frontend_dist: Path | None = None,
) -> FastAPI:
    selected_url = database_url or get_settings().database_url
    engine: Engine = make_engine(selected_url)
    session_factory: sessionmaker[Session] = create_session_factory(engine)
    static_dir = frontend_dist or PROJECT_ROOT / "frontend" / "dist"
    database_path = engine.url.database
    checkpoint_path = (
        Path(database_path).with_name("agent_checkpoints.sqlite3")
        if database_path and database_path != ":memory:"
        else None
    )
    runtime = ResearchRuntime(session_factory, checkpoint_path)
    browser_login = BrowserLoginManager(get_settings())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if run_migrations_on_startup:
            upgrade_database(selected_url)
        runtime.start()
        try:
            yield
        finally:
            await asyncio.to_thread(browser_login.close)
            await asyncio.to_thread(runtime.close)
            engine.dispose()

    application = FastAPI(
        title="Shopping Agent API",
        version="0.1.0",
        description="本地购物比较任务与商品链接服务。",
        lifespan=lifespan,
    )
    application.state.engine = engine
    application.state.session_factory = session_factory
    application.state.research_runtime = runtime
    application.state.browser_presence = BrowserPresenceMonitor()
    application.state.browser_login = browser_login
    application.include_router(api_router)
    application.include_router(research_router)
    application.include_router(browser_router)
    application.include_router(login_router)

    @application.get("/health/ready", tags=["system"])
    def readiness() -> dict[str, str]:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="数据库尚未就绪。",
            ) from exc
        return {"status": "ready"}

    @application.get(
        "/api/v1/collection/status", tags=["system"], response_model=CollectionAvailability
    )
    def collection_status() -> dict:
        return runtime.collector.availability()

    if static_dir.is_dir():
        assets_dir = static_dir / "assets"
        if assets_dir.is_dir():
            application.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
        favicon = static_dir / "favicon.svg"
        if favicon.is_file():
            application.add_api_route(
                "/favicon.svg",
                lambda: FileResponse(favicon),
                methods=["GET"],
                include_in_schema=False,
            )

        @application.get("/{path:path}", include_in_schema=False)
        def serve_frontend(path: str) -> Response:
            requested_path = (static_dir / path).resolve()
            try:
                requested_path.relative_to(static_dir.resolve())
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="页面不存在。") from exc
            if requested_path.is_file():
                return FileResponse(requested_path, headers={"Cache-Control": "no-store"})
            index_file = static_dir / "index.html"
            if index_file.is_file():
                return FileResponse(index_file, headers={"Cache-Control": "no-store"})
            raise HTTPException(status_code=404, detail="页面不存在。")

    return application


app = create_app()
