import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

# Expected application schemas defined in SA-4 / SA-14
EXPECTED_SCHEMAS = ("core", "iit", "isb", "isa", "knowledge")

# Create the async engine with connection pooling (SA-16)
engine = create_async_engine(
    url=settings.async_database_url,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,
    echo=settings.DB_ECHO,
)

# Async session factory for dependency injection
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def close_db_pool() -> None:
    """Closes all database connections in the pool on application shutdown."""
    logger.info("Closing PostgreSQL connection pool...")
    await engine.dispose()
    logger.info("PostgreSQL connection pool closed.")


async def check_db_health() -> dict:
    """
    Verifies database connectivity, checks installed extensions (pgvector),
    validates presence of expected schemas, and reports connection pool status.
    """
    pool = engine.pool
    pool_status = {
        "size": pool.size(),
        "checkedin": pool.checkedin(),
        "checkedout": pool.checkedout(),
        "overflow": pool.overflow(),
    }

    try:
        async with engine.connect() as conn:
            # 1. Simple ping
            await conn.execute(text("SELECT 1"))

            # 2. Check pgvector extension
            ext_result = await conn.execute(
                text("SELECT extname, extversion FROM pg_extension WHERE extname = 'vector'")
            )
            ext_row = ext_result.fetchone()
            pgvector_installed = ext_row is not None
            pgvector_version = ext_row[1] if ext_row else None

            # 3. Check expected schemas (core, iit, isb, isa, knowledge)
            schemas_result = await conn.execute(
                text(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name = ANY(:schemas)"
                ),
                {"schemas": list(EXPECTED_SCHEMAS)},
            )
            found_schemas = [row[0] for row in schemas_result.fetchall()]

            # 4. Check current database and connected user
            id_result = await conn.execute(
                text("SELECT current_database(), current_user, version()")
            )
            id_row = id_result.fetchone()
            current_db = id_row[0] if id_row else None
            current_user = id_row[1] if id_row else None
            pg_version = id_row[2] if id_row else None

            all_schemas_present = set(EXPECTED_SCHEMAS).issubset(set(found_schemas))

            return {
                "status": "connected" if all_schemas_present and pgvector_installed else "degraded",
                "database": current_db,
                "user": current_user,
                "postgres_version": pg_version,
                "pgvector": {
                    "installed": pgvector_installed,
                    "version": pgvector_version,
                },
                "schemas": {
                    "expected": list(EXPECTED_SCHEMAS),
                    "found": sorted(found_schemas),
                    "all_present": all_schemas_present,
                },
                "connection_pool": pool_status,
            }

    except Exception as exc:
        logger.warning("Database health check failed: %s", exc)
        return {
            "status": "disconnected",
            "error": str(exc),
            "connection_pool": pool_status,
        }
