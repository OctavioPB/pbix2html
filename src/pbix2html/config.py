"""Configuration via environment variables (.env). Never credentials in code."""
from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    teradata_host: str = os.getenv("TERADATA_HOST", "")
    teradata_user: str = os.getenv("TERADATA_USER", "")
    teradata_password: str = os.getenv("TERADATA_PASSWORD", "")
    teradata_logmech: str = os.getenv("TERADATA_LOGMECH", "TD2")
    teradata_database: str = os.getenv("TERADATA_DATABASE", "")
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
    echarts_cdn: str = os.getenv("ECHARTS_CDN", "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js")
    api_base: str = os.getenv("API_BASE", "http://localhost:8000")

    @property
    def has_teradata(self) -> bool:
        return bool(self.teradata_host and self.teradata_user)


settings = Settings()
