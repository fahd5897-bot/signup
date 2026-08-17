"""Application settings, typed and validated at import time.

Fail fast: a missing or malformed environment variable raises here, at process
start, rather than surfacing as a 500 on the first request that needs it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # ---------------------------------------------------------------- runtime
    environment: Literal["local", "staging", "production"] = "local"
    debug: bool = False

    # ------------------------------------------------------------------- LLM
    anthropic_api_key: SecretStr
    llm_model_generation: str = "claude-opus-5"
    llm_model_classify: str = "claude-haiku-4-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    llm_max_tokens: int = 64_000

    # ------------------------------------------------------------ embeddings
    voyage_api_key: SecretStr
    embedding_model: str = "voyage-multilingual-2"
    rerank_model: str = "rerank-2"
    #: Must match the deployed embedding model. Changing it invalidates the
    #: collection — `EMBEDDING_DIM` is baked into the Qdrant vector params and a
    #: mismatch is detected at startup rather than at query time.
    embedding_dim: int = 1024

    # -------------------------------------------------------------- datastores
    postgres_dsn: PostgresDsn
    redis_url: RedisDsn
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr | None = None
    qdrant_collection: str = "kb_chunks"
    qdrant_timeout: int = 30

    # ------------------------------------------------------- grounding policy
    min_retrieval_score: float = Field(default=0.35, ge=0.0, le=1.0)
    min_citation_coverage: float = Field(default=0.90, ge=0.0, le=1.0)
    abstain_on_ungrounded: bool = True
    require_human_approval: bool = True

    # -------------------------------------------------------------------- app
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900
    default_locale: Literal["en", "ar"] = "en"
    supported_locales: list[str] = ["en", "ar"]

    @field_validator("supported_locales", mode="before")
    @classmethod
    def _split_locales(cls, v: object) -> object:
        """Accept `SUPPORTED_LOCALES=en,ar` from the environment."""
        return [s.strip() for s in v.split(",") if s.strip()] if isinstance(v, str) else v


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Use as a FastAPI dependency: `Depends(get_settings)`."""
    return Settings()  # type: ignore[call-arg]  # values supplied by env
