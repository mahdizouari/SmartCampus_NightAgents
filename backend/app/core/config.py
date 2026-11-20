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

    # LLM Provider & Generation Settings (SA-36)
    LLM_PROVIDER: str = "openai_compatible"  # openai_compatible | ollama | mock
    LLM_BASE_URL: str = "https://api.groq.com/openai/v1"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "openai/gpt-oss-120b"
    LLM_TEMPERATURE: float = 0.2
    LLM_MAX_TOKENS: int = 1024
    LLM_TIMEOUT_SECONDS: float = 30.0
    LLM_FALLBACK_TO_MOCK: bool = True

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
