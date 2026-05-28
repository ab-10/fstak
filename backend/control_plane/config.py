from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FSTAK_", env_file=("../.env", ".env"), extra="ignore")

    domain_suffix: str = ""
    api_hostname: str = "api.fstak.runspx.com"

    database_url: str = ""

    caddy_admin_url: str = ""

    gcs_bucket_name: str = ""

    spx_api_url: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if not settings.domain_suffix:
        raise RuntimeError(
            "FSTAK_DOMAIN_SUFFIX must be set; control plane cannot operate without a domain suffix"
        )
    if not settings.spx_api_url:
        raise RuntimeError(
            "FSTAK_SPX_API_URL must be set; control plane delegates auth to SPX "
            "and cannot operate without an SPX API URL"
        )
    return settings
