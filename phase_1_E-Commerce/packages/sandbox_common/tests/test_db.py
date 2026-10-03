import os
from unittest.mock import AsyncMock, patch

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from sandbox_common.db import PoolConfigError, create_pool, db_readiness_check, ping_db
from sandbox_common.health import health_router
from sandbox_common.settings import Settings


def _settings(database_url: str | None) -> Settings:
    return Settings(service_name="sandbox_common", database_url=database_url)


async def test_create_pool_without_url_does_not_connect() -> None:
    settings = _settings(None)
    with patch("sandbox_common.db.asyncpg.create_pool", new_callable=AsyncMock) as open_pool:
        with pytest.raises(PoolConfigError):
            await create_pool(settings)
    open_pool.assert_not_called()


async def test_missing_pool_check_is_named_db() -> None:
    settings = _settings(None)
    check = db_readiness_check(lambda: None, settings)
    assert check.name == "db"
    with pytest.raises(RuntimeError, match="database pool is not open"):
        await check.check()


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is not set")
    return database_url


async def test_pool_ping_and_close() -> None:
    settings = _settings(_database_url())
    pool = await create_pool(settings)
    await ping_db(pool, settings)
    await pool.close()


async def test_readyz_follows_the_open_pool() -> None:
    settings = _settings(_database_url())
    holder: dict[str, asyncpg.Pool | None] = {"pool": await create_pool(settings)}
    app = FastAPI()
    app.include_router(
        health_router([db_readiness_check(lambda: holder["pool"], settings)])
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ready = await client.get("/readyz")
            assert ready.status_code == 200
            assert ready.json() == {"status": "ready"}

            await holder["pool"].close()
            holder["pool"] = None

            not_ready = await client.get("/readyz")
            assert not_ready.status_code == 503
            assert not_ready.json()["failed"] == ["db"]
            health = await client.get("/healthz")
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}
    finally:
        pool = holder["pool"]
        if pool is not None:
            await pool.close()
