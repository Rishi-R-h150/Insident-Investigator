from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import asyncpg

from sandbox_common.health import ReadinessCheck
from sandbox_common.settings import Settings


class PoolConfigError(RuntimeError):
    """Raised when a pool was requested without a usable DATABASE_URL."""


async def create_pool(settings: Settings) -> asyncpg.Pool:
    if settings.database_url is None:
        raise PoolConfigError("database url is required")
    return await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )


@asynccontextmanager
async def acquire(
    pool: asyncpg.Pool,
    settings: Settings,
) -> AsyncIterator[asyncpg.Connection]:
    async with pool.acquire(timeout=settings.db_acquire_timeout_ms / 1000) as conn:
        yield conn


async def ping_db(pool: asyncpg.Pool, settings: Settings) -> None:
    async with acquire(pool, settings) as conn:
        await conn.execute("SELECT 1")


def db_readiness_check(
    get_pool: Callable[[], asyncpg.Pool | None],
    settings: Settings,
) -> ReadinessCheck:
    async def check() -> None:
        pool = get_pool()
        if pool is None:
            raise RuntimeError("database pool is not open")
        await ping_db(pool, settings)

    return ReadinessCheck(name="db", check=check)
