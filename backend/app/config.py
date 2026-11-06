from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Either give full URLs, or the parts below (Docker/Unraid) and they're assembled.
    database_url: str = ""
    migration_database_url: str = ""
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "finance_tracker"
    db_app_password: str = "ft_app_dev"
    db_owner_password: str = "ft_owner_dev"
    # Optional superuser URL, used ONLY at startup to create roles/database on first run.
    db_admin_url: str = ""

    allow_registration: bool = True
    # auto = Secure flag only when the request arrived over HTTPS (works on plain-http LAN and behind TLS proxies)
    cookie_secure: Literal["auto", "true", "false"] = "auto"
    session_days: int = 30
    invite_days: int = 7
    max_upload_bytes: int = 20 * 1024 * 1024
    ai_worker: bool = True  # background categorisation worker
    app_version: str = ""  # set by the Docker build from the git tag; else the package version

    def _url(self, role: str, pw: str) -> str:
        return f"postgresql+psycopg://{role}:{quote(pw, safe='')}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def app_db_url(self) -> str:
        return self.database_url or self._url("ft_app", self.db_app_password)

    @property
    def owner_db_url(self) -> str:
        return self.migration_database_url or self._url("ft_owner", self.db_owner_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def app_version() -> str:
    v = get_settings().app_version.strip()
    if v:
        return v if v.startswith("v") or v == "dev" else "v" + v
    try:
        from importlib.metadata import version
        return "v" + version("finance-tracker") + "-dev"
    except Exception:
        return "dev"
