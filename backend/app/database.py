from collections.abc import Generator
from pathlib import Path

from fastapi import Request
from sqlalchemy import URL, Engine, create_engine, event, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def make_engine(database_url: URL | str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    parsed_url = make_url(url) if isinstance(url, str) else url
    if parsed_url.drivername.startswith("sqlite"):
        engine = create_engine(parsed_url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def enable_sqlite_features(connection, _record) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            if parsed_url.database != ":memory:":
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        return engine
    return create_engine(parsed_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ensure_database_directory(database_url: URL | str) -> None:
    url = make_url(database_url) if isinstance(database_url, str) else database_url
    database = url.database
    is_sqlite = url.drivername.startswith("sqlite")
    if is_sqlite and database and database != ":memory:":
        Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def get_session(request: Request) -> Generator[Session, None, None]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session
