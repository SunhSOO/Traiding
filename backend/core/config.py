"""Application settings loaded from environment / .env file.

We use pydantic-settings so that every config value is typed, validated
once at startup, and easy to override per environment without touching
code. The `Settings` instance is a singleton accessed via `get_settings()`.

Hard rules enforced here:
- `RUNTIME_MODE=live` requires `JWT_SECRET_KEY` to be set and at least
  one LLM provider to be configured. We block startup otherwise so the
  system never enters a state where live orders can fire without
  the audit/analysis pipeline behind them.
- `JWT_SECRET_KEY` falls back to a deterministic dev value ONLY when
  `APP_ENV=local`. In any other env an empty secret raises.
"""
from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class AppEnv(str, Enum):
    LOCAL = "local"
    STAGING = "staging"
    PROD = "prod"


class LogFormat(str, Enum):
    JSON = "json"
    CONSOLE = "console"


BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    """Centralised, validated configuration.

    Values are read from environment variables first, then from
    `backend/.env` (if present). The order matches pydantic-settings'
    default precedence: init args > env > .env > defaults.
    """

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Runtime ──
    runtime_mode: RuntimeMode = RuntimeMode.PAPER
    app_env: AppEnv = AppEnv.LOCAL
    timezone: str = "Asia/Seoul"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: LogFormat = LogFormat.JSON

    # ── Database ──
    database_url: str = "postgresql+psycopg://woonam:woonam@localhost:5432/woonam"
    database_url_async: str = "postgresql+asyncpg://woonam:woonam@localhost:5432/woonam"
    db_echo_sql: bool = False

    # ── Auth ──
    jwt_secret_key: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    bootstrap_username: str = "woonam"
    bootstrap_password: SecretStr = SecretStr("")

    # ── CORS ──
    allowed_origins: str = "http://localhost:8000,http://localhost:8080"

    # ── KR data sources ──
    dart_api_key: SecretStr = SecretStr("")
    bigkinds_access_key: SecretStr = SecretStr("")
    naver_client_id: str = ""
    naver_client_secret: SecretStr = SecretStr("")
    bok_ecos_api_key: SecretStr = SecretStr("")

    # ── Historical news backfill knobs ──
    news_backfill_job_id: str = ""
    news_backfill_window_days: int = 365

    # ── US data sources ──
    sec_user_agent: str = ""
    fred_api_key: SecretStr = SecretStr("")

    # ── LLM providers ──
    ollama_host: str = "http://localhost:11434"
    ollama_default_model: str = "qwen2.5:14b"
    groq_api_key: SecretStr = SecretStr("")
    groq_default_model: str = "llama-3.3-70b-versatile"
    gemini_api_key: SecretStr = SecretStr("")
    gemini_default_model: str = "gemini-1.5-flash"

    # ── MT5 ──
    mt5_login: str = ""
    mt5_password: SecretStr = SecretStr("")
    mt5_server: str = ""

    # ── KIS (한국투자증권 OpenAPI — real KR equity broker) ──
    # Free: open a KIS account, apply for OpenAPI at apiportal.koreainvestment.com,
    # create a 모의투자(paper) account first. Account format "12345678-01".
    kis_app_key: SecretStr = SecretStr("")
    kis_app_secret: SecretStr = SecretStr("")
    kis_account_no: SecretStr = SecretStr("")
    kis_paper: bool = True   # default 모의투자; real-money also needs RUNTIME_MODE=live

    # ── Storage paths ──
    data_dir: Path = BACKEND_DIR / "var" / "data"
    model_dir: Path = BACKEND_DIR / "var" / "models"
    log_dir: Path = BACKEND_DIR / "var" / "logs"

    # ── Universe ──
    kr_indices: str = "KOSPI200,KOSDAQ150"
    us_indices: str = "SP500,NASDAQ100"

    # ── Derived helpers ──
    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def kr_indices_list(self) -> list[str]:
        return [s.strip().upper() for s in self.kr_indices.split(",") if s.strip()]

    @property
    def us_indices_list(self) -> list[str]:
        return [s.strip().upper() for s in self.us_indices.split(",") if s.strip()]

    @property
    def has_ollama(self) -> bool:
        return bool(self.ollama_host)

    @property
    def has_groq(self) -> bool:
        return bool(self.groq_api_key.get_secret_value())

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key.get_secret_value())

    @property
    def configured_llm_providers(self) -> list[str]:
        providers = []
        if self.has_ollama:
            providers.append("ollama")
        if self.has_groq:
            providers.append("groq")
        if self.has_gemini:
            providers.append("gemini")
        return providers

    # ── Validators ──
    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, v: str) -> str:
        # Importing zoneinfo lazily so we don't pay the cost on every config load
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as e:
            raise ValueError(f"Unknown IANA timezone: {v}") from e
        return v

    @model_validator(mode="after")
    def _validate_safety_gates(self) -> "Settings":
        """Block dangerous combinations at startup."""
        # Live mode requires a real JWT secret.
        if self.runtime_mode == RuntimeMode.LIVE and not self.jwt_secret_key.get_secret_value():
            raise ValueError(
                "RUNTIME_MODE=live requires JWT_SECRET_KEY to be set. "
                "Generate one with `python -c 'import secrets; print(secrets.token_hex(32))'`."
            )

        # Live mode requires at least one LLM provider (information module won't function otherwise).
        if self.runtime_mode == RuntimeMode.LIVE and not self.configured_llm_providers:
            raise ValueError(
                "RUNTIME_MODE=live requires at least one LLM provider configured "
                "(ollama_host / groq_api_key / gemini_api_key)."
            )

        # Non-local env without a real JWT secret is unsafe even in paper mode.
        if self.app_env != AppEnv.LOCAL and not self.jwt_secret_key.get_secret_value():
            raise ValueError(
                f"APP_ENV={self.app_env.value} requires JWT_SECRET_KEY to be set."
            )

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Call this everywhere; the cache makes
    it cheap. Tests can reset via `get_settings.cache_clear()`."""
    return Settings()


def reset_settings_cache() -> None:
    """For tests: force re-read of environment on next call."""
    get_settings.cache_clear()
