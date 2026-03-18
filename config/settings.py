"""config/settings.py
Central application configuration loaded from environment variables.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings  # pip: pydantic-settings


class Settings(BaseSettings):
    # ── Groq ──────────────────────────────────────────────────────────────────
    groq_api_key: str = Field(..., env="GROQ_API_KEY")

    # ── Application ───────────────────────────────────────────────────────────
    app_host: str = Field("0.0.0.0", env="APP_HOST")
    app_port: int = Field(8000, env="APP_PORT")
    app_env: str = Field("development", env="APP_ENV")

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        "sqlite+aiosqlite:///./data/app.db", env="DATABASE_URL"
    )

    # ── Auth / JWT ────────────────────────────────────────────────────────────
    jwt_secret_key: str = Field(..., env="JWT_SECRET_KEY")
    jwt_algorithm: str = Field("HS256", env="JWT_ALGORITHM")
    jwt_expire_hours: int = Field(8, env="JWT_EXPIRE_HOURS")

    # ── Config template ───────────────────────────────────────────────────────
    config_template: str = Field("tech_company.yaml", env="CONFIG_TEMPLATE")

    # ── File uploads ──────────────────────────────────────────────────────────
    upload_dir: str = Field("uploads", env="UPLOAD_DIR")

    # ── Derived paths (not env-backed) ────────────────────────────────────────
    @property
    def templates_dir(self) -> Path:
        return Path(__file__).parent / "templates"

    @property
    def active_template_path(self) -> Path:
        return self.templates_dir / self.config_template

    @property
    def upload_path(self) -> Path:
        return Path(self.upload_dir)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (reads .env once)."""
    return Settings()
