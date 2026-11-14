"""TERRA-X configuration. Every secret is optional; the core DEM workflow has no paid dependency."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_MODULE_PATH = Path(__file__).resolve()


def _resolve_repo_root(module_path: Path) -> Path:
    """Return the repository root in a checkout, or the app root inside a container.

    Checkout: ``<repo>/apps/api/terrax/config.py`` -> ``<repo>``
    Image:    ``/app/terrax/config.py``            -> ``/app``

    ``parents[3]`` raises IndexError for the shallow image layout, which previously made
    the API and worker fail at import time inside Docker.
    """
    parents = module_path.parents
    return parents[3] if len(parents) > 3 else parents[1]


REPO_ROOT = _resolve_repo_root(_MODULE_PATH)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", Path.cwd() / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    terrax_env: str = "development"
    terrax_log_level: str = "INFO"
    terrax_api_base_url: str = "http://localhost:8000"
    terrax_public_api_url: str = "http://localhost:8000"
    terrax_cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:3001,http://127.0.0.1:3001"
    )
    terrax_cors_origin_regex: str | None = None

    database_url: str = "postgresql+psycopg://terrax:terrax@localhost:5434/terrax"
    data_dir: Path = Field(default=REPO_ROOT / "data", validation_alias="TERRAX_DATA_DIR")

    terrax_cache_dir: Path | None = None
    terrax_report_dir: Path | None = None

    nasa_earthdata_token: str | None = None
    nasa_earthdata_username: str | None = None
    nasa_earthdata_password: str | None = None
    nasa_firms_map_key: str | None = None

    meteomatics_username: str | None = None
    meteomatics_password: str | None = None

    terrax_llm_provider: str = "none"
    terrax_llm_model: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    terrax_admin_api_key: str | None = None
    terrax_allow_anonymous_admin: bool = False
    terrax_rate_limit_per_minute: int = 120
    terrax_gazetteer_user_agent: str = "TERRA-X/0.1 (planetary analogue research)"

    terrax_worker_poll_seconds: float = 1.0
    terrax_job_max_attempts: int = 3
    terrax_worker_concurrency: int = 1

    redis_url: str | None = None

    # Scientific defaults. Configurable per analysis; these are research defaults, not constants.
    default_footprint_km: float = 40.0
    default_analysis_resolution_m: float = 500.0
    default_min_feature_coverage: float = 0.5
    default_max_candidates: int = 25
    default_screening_resolution_deg: float = 5.0
    default_screening_grid_km: float = 5.0
    default_cluster_radius_km: float = 100.0
    http_timeout_seconds: float = 60.0
    http_user_agent: str = "TERRA-X/0.1 (NASA Space Apps planetary analogue research)"

    @property
    def cache_dir(self) -> Path:
        return self.terrax_cache_dir or (self.data_dir / "cache")

    @property
    def report_dir(self) -> Path:
        return self.terrax_report_dir or (self.data_dir / "reports")

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.terrax_cors_origins.split(",") if o.strip()]

    @property
    def cors_origin_regex(self) -> str | None:
        """Allow local Next.js dev servers on any port without weakening non-local origins."""
        if self.terrax_cors_origin_regex is not None:
            return self.terrax_cors_origin_regex.strip() or None
        return r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"

    @property
    def earthdata_available(self) -> bool:
        return bool(self.nasa_earthdata_token or (self.nasa_earthdata_username and self.nasa_earthdata_password))

    @property
    def meteomatics_available(self) -> bool:
        return bool(self.meteomatics_username and self.meteomatics_password)

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.cache_dir, self.report_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
