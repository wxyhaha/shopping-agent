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
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=90)
    browser_timeout_seconds: float = Field(default=55.0, ge=15, le=120)
    # Product pages can be tens of thousands of pixels tall and load the detail section only
    # as it scrolls near. Six passes covered roughly a tenth of a Tmall page and left the
    # detail section unrendered, so the default sweep is longer.
    browser_max_scrolls: int = Field(default=12, ge=0, le=12)
    browser_trust_fake_ip_dns: bool = False
    # How long to wait for a lazily injected detail section before recording that it was not
    # seen. Tmall completes the document long before the panels arrive, so this is the only
    # signal; fixtures can set it low so tests do not spend the budget on pages without art.
    browser_detail_wait_seconds: float = Field(default=25.0, ge=1, le=60)
    # Read a font-obfuscated price from the rendered screenshot. Off by default so the
    # normal path never spends a vision request on pages that expose plain text.
    price_vision_enabled: bool = False
    # Read text from product detail artwork. Separate switch: it costs a vision request per
    # image and its output is merchant marketing copy, not verified specification.
    detail_vision_enabled: bool = False
    # Attach to a browser the user started themselves, so Taobao/Tmall pages that require a
    # login can be read with that browser's real session. The launched Chromium reports
    # navigator.webdriver=true, which is why those sites send it to a login wall and reject
    # the slider; a real browser the user signed into carries no such marker. Off by default
    # because it uses a browser outside this process and only works while it is running.
    browser_cdp_enabled: bool = False
    browser_cdp_url: str = "http://127.0.0.1:9222"
    # JD access is paused after the user's account restriction. Re-enabling browser work
    # requires an explicit local opt-in; it must never silently retry another login channel.
    jd_background_enabled: bool = False

    @property
    def browser_session_dir(self) -> Path:
        return self.data_dir / "browser-sessions"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "shopping_agent.sqlite3"

    @property
    def database_url(self) -> URL:
        return URL.create("sqlite", database=self.database_path.as_posix())


@lru_cache
def get_settings() -> Settings:
    return Settings()
