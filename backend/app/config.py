from functools import lru_cache
from pathlib import Path

from platformdirs import user_data_dir
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT_ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SHOPPING_AGENT_",
        env_file=ROOT_ENV_FILE,
        extra="ignore",
    )

    data_dir: Path = Path(user_data_dir("shopping-agent", appauthor=False))
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = Field(default=20.0, gt=0, le=90)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "shopping_agent.sqlite3"

    @property
    def database_url(self) -> URL:
        return URL.create("sqlite", database=self.database_path.as_posix())


@lru_cache
def get_settings() -> Settings:
    return Settings()
