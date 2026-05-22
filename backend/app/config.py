from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Project ──────────────────────────────────────
    PROJECT_NAME: str = "POE2 BD Agent"
    PROJECT_DIR: Path = Path(__file__).resolve().parent.parent
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ── PostgreSQL ────────────────────────────────────
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "poe2bd"
    POSTGRES_PASSWORD: str = "poe2bd_dev"
    POSTGRES_DB: str = "poe2bd"

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}"
            f":{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}"
            f"/{self.POSTGRES_DB}"
        )

    @property
    def postgres_sync_url(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}"
            f":{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}"
            f"/{self.POSTGRES_DB}"
        )

    # ── Neo4j ─────────────────────────────────────────
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "poe2bd_dev"
    NEO4J_DATABASE: str = "neo4j"

    # ── Redis ─────────────────────────────────────────
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    @property
    def redis_url(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # ── Qdrant ────────────────────────────────────────
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_GRPC_PORT: int = 6334
    QDRANT_API_KEY: str | None = None

    # ── Celery ────────────────────────────────────────
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""

    @property
    def celery_broker(self) -> str:
        return self.CELERY_BROKER_URL or self.redis_url

    @property
    def celery_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.redis_url

    # ── GGG API (POE2) ────────────────────────────────
    # 公共 API (league/trade): api.pathofexile.com
    # Web API (角色数据): www.pathofexile.com (需 POESESSID Cookie)
    # 注意: POE2 暂无官方 Ladder API，角色 BD 主要来自社区数据源
    GGG_WEB_BASE_URL: str = "https://www.pathofexile.com"
    GGG_RATE_LIMIT_PER_MIN: int = 45
    GGG_USER_AGENT: str = "POE2BD-Agent/1.0 (contact: dev@example.com)"
    GGG_POESESSID: str = ""
    GGG_DEFAULT_LEAGUE: str = "Standard"

    # ── Community Data Sources ────────────────────────
    POEDB_BASE_URL: str = "https://poedb.tw"
    POBB_IN_API_URL: str = "https://pobb.in/api"

    # ── LLM / Anthropic ───────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 8192

    # ── Collection Schedule ───────────────────────────
    COLLECT_LADDER_INTERVAL_HOURS: int = 168  # 每周
    COLLECT_CHARACTER_INTERVAL_HOURS: int = 168
    COLLECT_MECHANICS_INTERVAL_HOURS: int = 168

    # ── Build Generation ──────────────────────────────
    MAX_REFERENCE_BUILDS: int = 5
    BUILD_CONFIDENCE_THRESHOLD: float = 0.6


settings = Settings()
