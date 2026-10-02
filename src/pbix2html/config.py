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
    # A matrix grouped by several dimensions can return far more rows than a browser can draw —
    # every row becomes a <tr>. Rows past this are not fetched, and the visual says so rather
    # than quietly showing a partial table. 0 means no limit.
    max_rows: int = int(os.getenv("MAX_ROWS", "20000"))
    echarts_cdn: str = os.getenv("ECHARTS_CDN", "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js")
    api_base: str = os.getenv("API_BASE", "http://localhost:8000")
    hah_base_dev: str = os.getenv("HAH_BASE_DEV", "https://transcend-k8s-dev.td.teradata.com/dev-html-app-host")
    hah_base_uat: str = os.getenv("HAH_BASE_UAT", "https://transcend-k8s-dev.td.teradata.com/html-app-host")
    hah_base_prd: str = os.getenv("HAH_BASE_PRD", "https://transcend-k8s.td.teradata.com/html-app-host")

    @property
    def has_teradata(self) -> bool:
        return bool(self.teradata_host and self.teradata_user)

    @property
    def hah_bases(self) -> dict[str, str]:
        """--mode hah environments (ADR-004); not used by snapshot/live."""
        return {"dev": self.hah_base_dev, "uat": self.hah_base_uat, "prd": self.hah_base_prd}


settings = Settings()
