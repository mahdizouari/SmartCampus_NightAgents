from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment or .env file."""

    PROJECT_NAME: str = "SmartCampus AI"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # CORS origins: allowed URLs (e.g. Next.js on port 3000)
    BACKEND_CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # Security / JWT / OAuth Settings (SA-20 & SA-28)
    SECRET_KEY: str = "change_me_in_production_use_a_long_random_string"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    OTP_EXPIRE_SECONDS: int = 300
    GOOGLE_OAUTH_CLIENT_ID: str = ""

    # Database Settings (PostgreSQL 16 + pgvector)
    POSTGRES_SERVER: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "smartcampus_app"
    POSTGRES_PASSWORD: str = "smartcampus_app_secret"
    POSTGRES_DB: str = "smartcampus_db"
    DATABASE_URL: str | None = None

    # Connection pooling parameters (SA-16)
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800
    DB_ECHO: bool = False

    # Redis Settings (Hot memory & session store)
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None
    REDIS_URL: str | None = None
    REDIS_CHAT_HISTORY_TTL: int = 604800  # 7 days in seconds
    REDIS_CHAT_MAX_MESSAGES: int = 16  # 8 turns (16 messages max)
    REDIS_GUEST_SESSION_TTL: int = 86400  # 24 hours in seconds (SA-26 default)

    # Signed Anonymous Guest Session & Rate Limiting Settings (SA-25)
    GUEST_SESSION_COOKIE_NAME: str = "smartcampus_guest_session"
    GUEST_SESSION_TTL_SECONDS: int = 604800  # 7-day sliding TTL after last activity
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    RATE_LIMIT_GUEST_IP_PER_MINUTE: int = 30
    RATE_LIMIT_GUEST_SESSION_PER_MINUTE: int = 30
    RATE_LIMIT_GUEST_CHAT_PER_MINUTE: int = 15
    RATE_LIMIT_STUDENT_PER_MINUTE: int = 120
    RATE_LIMIT_STAFF_PER_MINUTE: int = 300

    # LLM Provider & Generation Settings (SA-36 — 100% Open-Source <=9B via Local Ollama)
    LLM_PROVIDER: str = "ollama"  # ollama | openai_compatible | mock
    LLM_BASE_URL: str = "http://host.docker.internal:11434/v1"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "qwen3:8b"  # Or qwen2.5:7b-instruct / smartcampus-vision-8b (QLoRA GGUF)
    LLM_ENABLE_THINKING: bool = False  # Disable Qwen3 <think> chain-of-thought for fast <4s RAG
    LLM_TEMPERATURE: float = 0.2
    LLM_MAX_TOKENS: int = 1024
    LLM_TIMEOUT_SECONDS: float = 60.0
    LLM_FALLBACK_TO_MOCK: bool = True

    # VLM Transcript Extraction Settings (Bonus-1 / SA-42: VISION 1.4 Reader via Ollama)
    VLM_PROVIDER: str = "ollama"  # ollama | openai_compatible | mock
    VLM_BASE_URL: str = ""  # Defaults to host Ollama / LLM_BASE_URL when empty
    VLM_API_KEY: str = ""  # Empty for local Ollama
    VLM_MODEL: str = "qwen2.5vl:7b"  # Open-source <=8B Apache 2.0 (or qwen3-vl:8b)
    VLM_TIMEOUT_SECONDS: float = 90.0
    VLM_CONFIDENCE_THRESHOLD: float = 0.85
    VLM_MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB upload cap
    VLM_FALLBACK_TO_LOCAL_PARSER: bool = True

    # RAG Embeddings & Reranking Settings (SA-54 / SA-61: BAAI/bge-m3 1024d + pgvector)
    EMBEDDING_PROVIDER: str = "ollama"  # ollama | openai_compatible | mock
    EMBEDDING_BASE_URL: str = ""  # Defaults to LLM_BASE_URL when empty
    EMBEDDING_API_KEY: str = ""  # Defaults to LLM_API_KEY when empty
    EMBEDDING_MODEL: str = "bge-m3"
    EMBEDDING_DIMENSION: int = 1024
    EMBEDDING_TIMEOUT_SECONDS: float = 30.0
    EMBEDDING_FALLBACK_TO_MOCK: bool = True
    RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RAG_MIN_RELEVANCE_SCORE: float = 0.25

    # Speech-to-Text / Whisper Audio Transcription (Bonus-3: Voice Notes)
    WHISPER_PROVIDER: str = "openai_compatible"  # openai_compatible | local | mock
    OPENAI_API_KEY: str = ""
    STT_API_KEY: str = ""
    WHISPER_API_KEY: str = ""
    WHISPER_MODEL: str = "whisper-large-v3"
    WHISPER_BASE_URL: str = ""  # Defaults to local :8001/v1 or LLM_BASE_URL
    WHISPER_TIMEOUT_SECONDS: float = 60.0

    # Binary File & Object Storage Adapter Settings (SA-50)
    STORAGE_ROOT: str = "storage"
    STORAGE_BUCKET_KNOWLEDGE: str = "knowledge"
    STORAGE_BUCKET_CANDIDATES: str = "candidates"
    MAX_DOCUMENT_FILE_SIZE_BYTES: int = 25 * 1024 * 1024  # 25 MB

    @property
    def effective_storage_root(self) -> Path:
        """Returns the absolute root directory for local object storage (SA-50)."""
        root_path = Path(self.STORAGE_ROOT)
        if not root_path.is_absolute():
            # Resolve relative to backend base dir or current working dir
            return Path.cwd().resolve() / root_path
        return root_path.resolve()

    @property
    def effective_vlm_base_url(self) -> str:
        """Returns the VLM base URL (defaults to host Ollama when VLM_PROVIDER=ollama)."""
        if self.VLM_BASE_URL.strip():
            return self.VLM_BASE_URL.strip().rstrip("/")
        if (
            self.VLM_PROVIDER.lower().strip() == "ollama"
            and self.LLM_PROVIDER.lower().strip() != "ollama"
        ):
            return "http://host.docker.internal:11434/v1"
        return self.LLM_BASE_URL.strip().rstrip("/")

    @property
    def effective_vlm_api_key(self) -> str:
        """Returns the VLM API key (inherits LLM_API_KEY if unset)."""
        return self.VLM_API_KEY.strip() or self.LLM_API_KEY.strip()

    @property
    def effective_embedding_base_url(self) -> str:
        """Returns the embedding API base URL (defaults to host Ollama when provider=ollama)."""
        if self.EMBEDDING_BASE_URL.strip():
            return self.EMBEDDING_BASE_URL.strip().rstrip("/")
        if (
            self.EMBEDDING_PROVIDER.lower().strip() == "ollama"
            and self.LLM_PROVIDER.lower().strip() != "ollama"
        ):
            return "http://host.docker.internal:11434/v1"
        return self.LLM_BASE_URL.strip().rstrip("/")

    @property
    def effective_embedding_api_key(self) -> str:
        """Returns the embedding API key (inherits LLM_API_KEY if unset)."""
        return self.EMBEDDING_API_KEY.strip() or self.LLM_API_KEY.strip()

    @property
    def effective_whisper_base_url(self) -> str:
        """Return Whisper STT base URL (local :8001/v1 when local, else LLM_BASE_URL)."""
        if self.WHISPER_BASE_URL.strip():
            return self.WHISPER_BASE_URL.strip().rstrip("/")
        if self.WHISPER_PROVIDER.strip().lower() == "local":
            return "http://host.docker.internal:8001/v1"
        return self.LLM_BASE_URL.strip().rstrip("/")

    @property
    def effective_whisper_api_key(self) -> str:
        """Returns the Whisper API key (checks STT_API_KEY / OPENAI_API_KEY, else LLM_API_KEY)."""
        return (
            self.WHISPER_API_KEY.strip()
            or self.STT_API_KEY.strip()
            or self.OPENAI_API_KEY.strip()
            or self.LLM_API_KEY.strip()
        )

    @property
    def effective_redis_url(self) -> str:
        """Returns the Redis connection URL."""
        if self.REDIS_URL:
            return self.REDIS_URL
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def async_database_url(self) -> str:
        """Returns the async database URL, formatted for asyncpg driver."""
        if self.DATABASE_URL:
            url = self.DATABASE_URL
            if url.startswith("postgresql://"):
                return url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif url.startswith("postgres://"):
                return url.replace("postgres://", "postgresql+asyncpg://", 1)
            return url
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",") if i.strip()]
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
