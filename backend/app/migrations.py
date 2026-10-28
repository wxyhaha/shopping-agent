from alembic import command
from alembic.config import Config
from sqlalchemy import URL

from app.config import PROJECT_ROOT
from app.database import ensure_database_directory


def upgrade_database(database_url: URL | str) -> None:
    ensure_database_directory(database_url)
    config = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
    rendered_url = str(database_url)
    config.set_main_option("sqlalchemy.url", rendered_url.replace("%", "%%"))
    command.upgrade(config, "head")
