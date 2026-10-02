# sandbox_common — build spec

This is the spec for the first slice of `sandbox_common`. Build only what is written here. When every check in [Done](#done) passes, start `auth`. Leave telemetry, the HTTP client, seeded randomness, and chaos hooks for later slices; the module map at the end reserves names for them.

`sandbox_common` is a library. It does not listen on a port. `auth` and the other six services import it so they start, log, talk to Postgres, and answer health probes the same way.

## What this slice provides

| Piece | Module | Job |
|---|---|---|
| Settings | `settings.py` | Read the shared environment variables into one typed object. |
| JSON logs | `logging.py` | Write one JSON object per log line to stdout. |
| Postgres pool | `db.py` | Open an asyncpg pool and run a readiness ping through it. |
| Health routes | `health.py` | `GET /healthz` and `GET /readyz`. |

A service keeps its own routes, SQL, and migrations. It calls this library from startup.

## Layout

Create this under `phase_1_E-Commerce/`:

```text
phase_1_E-Commerce/
  requirements.txt                          # already present; leave it as the sandbox pin list
  docs/
    sandbox_common.md                       # this file
  packages/
    sandbox_common/
      pyproject.toml
      src/sandbox_common/
        __init__.py
        settings.py
        logging.py
        db.py
        health.py
      tests/
        test_settings.py
        test_logging.py
        test_health.py
        test_db.py
```

`requirements.txt` stays the list of versions for the whole sandbox. `packages/sandbox_common/pyproject.toml` repeats only the packages this library imports, at those same versions.

## Install and test

From `phase_1_E-Commerce`, using the existing `.venv`:

```bash
.venv/bin/pip install setuptools
.venv/bin/pip install -e packages/sandbox_common
.venv/bin/pytest packages/sandbox_common/tests
```

`pip install -e` puts `sandbox_common` on the venv path, so `auth` can `import sandbox_common` the same way.

`pyproject.toml`:

```toml
[project]
name = "sandbox-common"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi==0.142.2",
  "pydantic==2.13.5",
  "pydantic-settings==2.15.0",
  "asyncpg==0.31.0",
  "structlog==26.1.0",
]

[project.optional-dependencies]
dev = [
  "pytest==9.1.1",
  "pytest-asyncio==1.4.0",
  "httpx==0.28.1",
]

[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["src"]
```

## Decisions already made

Use these as written. They match the Phase 1 design and the pinned libraries in the venv.

- Python 3.12, async, type hints on every public function.
- Settings come from environment variables via pydantic-settings. Unknown variables are ignored, so a service may set `REDIS_URL` or `PORT` in the same process.
- Logs are structlog JSON on stdout. The event name is the `event` field.
- The pool is asyncpg. Query code and Alembic stay in the service that owns the database.
- The DSN scheme is `postgresql://` or `postgres://`. That is the asyncpg form. Alembic, later, may use a SQLAlchemy URL in the service. This package accepts the asyncpg form only.
- Pool defaults are the design defaults: min 5, max 20, acquire timeout 1000 ms.
- `db.acquire_timeout_ms` is the wait to **borrow** a connection from the pool. Pass it to `pool.acquire(timeout=...)`. asyncpg's connect timeout (default 60 s) is a different knob and stays untouched in this slice.
- `/healthz` answers whether the process is serving. `/readyz` answers whether the registered dependency checks pass.
- Readiness check names are supplied by the service (`"db"`, later `"redis"`). This package knows how to ping Postgres. It does not open Redis.
- A failed readiness check returns HTTP 503. The JSON body lists check names. The exception text stays in the log, because a database error can contain the host and database name.
- If Postgres is down at startup, the process still boots. `/healthz` returns 200 and `/readyz` returns 503. The service lifespan catches the connection error and stores `pool = None`. A missing `DATABASE_URL` on a service that asked for a pool is a programming error and still raises.

## Public surface

`src/sandbox_common/__init__.py` exports only these names:

```python
from sandbox_common.db import (
    PoolConfigError,
    acquire,
    create_pool,
    db_readiness_check,
    ping_db,
)
from sandbox_common.health import ReadinessCheck, health_router
from sandbox_common.logging import configure_logging, get_logger
from sandbox_common.settings import Settings, get_settings

__all__ = [
    "PoolConfigError",
    "ReadinessCheck",
    "Settings",
    "acquire",
    "configure_logging",
    "create_pool",
    "db_readiness_check",
    "get_logger",
    "get_settings",
    "health_router",
    "ping_db",
]
```

Build the modules in the order below. Each section's tests should pass before you start the next module.

---

## 1. Settings — `settings.py`

### Behavior

`Settings` is a `pydantic_settings.BaseSettings` model.

| Field | Environment variable | Type | Default | Rule |
|---|---|---|---|---|
| `service_name` | `SERVICE_NAME` | `str` | required | Non-empty after stripping whitespace. |
| `log_level` | `LOG_LEVEL` | `str` | `"INFO"` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`. Store the uppercase value. |
| `database_url` | `DATABASE_URL` | `str \| None` | `None` | `None` or a URL whose scheme is `postgresql` or `postgres`. |
| `db_pool_min` | `DB_POOL_MIN` | `int` | `5` | `>= 1` |
| `db_pool_max` | `DB_POOL_MAX` | `int` | `20` | `>= db_pool_min` |
| `db_acquire_timeout_ms` | `DB_ACQUIRE_TIMEOUT_MS` | `int` | `1000` | `>= 1` |

Model config:

- Ignore extra environment variables (`extra="ignore"`).
- Environment names are case-insensitive.
- Read the process environment. This slice does not load a `.env` file.

Validation failures raise `pydantic.ValidationError` while the object is being built. Fail at startup, before the first request.

`get_settings()` returns a cached `Settings()` so a process reads the environment once. Tests that change variables call `get_settings.cache_clear()` before calling it again. Constructing `Settings()` directly always reads the current environment and is the right call inside unit tests.

### Tests — `tests/test_settings.py`

Use `monkeypatch.setenv` / `monkeypatch.delenv`. Clear the cache in a fixture so tests stay isolated.

1. `SERVICE_NAME=auth` and no other variables produces `service_name="auth"`, `log_level="INFO"`, `database_url is None`, pool min 5, pool max 20, acquire timeout 1000.
2. A missing `SERVICE_NAME` raises `ValidationError`.
3. `SERVICE_NAME="  "` raises `ValidationError`.
4. `LOG_LEVEL=debug` is stored as `"DEBUG"`. `LOG_LEVEL=verbose` raises `ValidationError`.
5. `DATABASE_URL=postgresql://postgres:sandbox@localhost:5432/auth_db` is accepted. `DATABASE_URL=postgresql+asyncpg://postgres@localhost/auth_db` raises `ValidationError`.
6. `DB_POOL_MIN=0` raises. `DB_POOL_MAX=4` with `DB_POOL_MIN=5` raises. `DB_ACQUIRE_TIMEOUT_MS=0` raises.
7. `REDIS_URL=redis://localhost:6379/0` in the environment does not raise.
8. `get_settings()` returns the same object twice. After `cache_clear()` and a changed `SERVICE_NAME`, it returns the new name.

---

## 2. Logging — `logging.py`

### Behavior

```python
def configure_logging(settings: Settings) -> None: ...

def get_logger() -> structlog.stdlib.BoundLogger: ...
```

`configure_logging` is called once at process startup. A second call replaces the previous configuration and rebinds `service`.

Configuration:

- Render each event as one JSON object, followed by a newline, on stdout.
- Add an ISO-8601 UTC `timestamp` field.
- Add a `level` field (`info`, `warning`, …) via structlog's `add_log_level`.
- Bind `service` from `settings.service_name` with `structlog.contextvars`, so every later log line includes it without the caller passing it.
- Drop events below `settings.log_level`. `INFO` keeps info, warning, and error. `WARNING` drops info.
- Use `structlog.PrintLoggerFactory()` so the line is the rendered JSON. Leave the stdlib root logger alone.

`get_logger()` returns the structlog logger. Callers write:

```python
log = get_logger()
log.info("login_ok", user_id="ada")
```

That line contains at least:

```json
{"timestamp": "2026-10-02T07:15:00.000000Z", "level": "info", "service": "auth", "event": "login_ok", "user_id": "ada"}
```

Key names are `timestamp`, `level`, `service`, and `event`. Extra keyword arguments become extra JSON fields.

### Tests — `tests/test_logging.py`

Capture stdout with `capsys`.

1. After `configure_logging` with `service_name="auth"` and `log_level="INFO"`, `get_logger().info("login_ok", user_id="ada")` writes a single line that `json.loads` accepts. The object has `event="login_ok"`, `service="auth"`, `level="info"`, `user_id="ada"`, and a `timestamp` string that ends with `Z` or contains `+00:00`.
2. With `log_level="WARNING"`, `log.info("quiet")` writes nothing, and `log.warning("loud")` writes one line with `event="loud"`.
3. Calling `configure_logging` a second time with `service_name="inventory"` makes the next line carry `service="inventory"`.

---

## 3. Health routes — `health.py`

### Behavior

```python
@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    check: Callable[[], Awaitable[None]]


def health_router(checks: Sequence[ReadinessCheck]) -> APIRouter: ...
```

`check` returns normally when the dependency is usable. It raises any exception when it is not. The router catches that exception.

Mount the router at the application root (`app.include_router(health_router(...))`) so the paths are `/healthz` and `/readyz`.

**`GET /healthz`**

- Always HTTP 200.
- Body: `{"status": "ok"}`.
- Runs no checks and touches no pool.

**`GET /readyz`**

- Runs every check, even after an earlier one has failed, so one response lists the full set.
- All checks pass, or `checks` is empty: HTTP 200, body `{"status": "ready"}`.
- Any check raises: HTTP 503, body `{"status": "not_ready", "failed": ["db", "redis"]}` where `failed` is the check names in registration order.
- For each failure, log `readiness_check_failed` at warning with fields `check` (the name) and `error_type` (the exception class name, such as `TimeoutError`). Omit the exception message from the HTTP body.
- A check that raises still produces 503. The exception does not become an unhandled 500.

Empty `checks` is the provider-mock case: the process has no dependencies, so it is ready as soon as it can serve.

### Tests — `tests/test_health.py`

Build a tiny `FastAPI()` app inside the test, include the router, and call it with `fastapi.testclient.TestClient`.

1. No checks: `/healthz` is 200 `{"status": "ok"}`, `/readyz` is 200 `{"status": "ready"}`.
2. A check that returns normally: `/readyz` is 200.
3. A check named `"db"` that raises `RuntimeError("password=secret")`: `/readyz` is 503, `failed == ["db"]`, and the response text does not contain `secret`.
4. Two checks, the first raises and the second raises: `failed` lists both names in order.
5. A failing check still leaves `/healthz` at 200.
6. With logging configured, a failing check emits a warning line whose JSON has `event="readiness_check_failed"`, `check="db"`, and `error_type="RuntimeError"`.

---

## 4. Postgres pool — `db.py`

### Behavior

```python
class PoolConfigError(RuntimeError):
    """Raised when a pool was requested without a usable DATABASE_URL."""


async def create_pool(settings: Settings) -> asyncpg.Pool: ...


@asynccontextmanager
async def acquire(pool: asyncpg.Pool, settings: Settings) -> AsyncIterator[asyncpg.Connection]: ...


async def ping_db(pool: asyncpg.Pool, settings: Settings) -> None: ...


def db_readiness_check(
    get_pool: Callable[[], asyncpg.Pool | None],
    settings: Settings,
) -> ReadinessCheck: ...
```

`create_pool`:

- When `settings.database_url` is `None`, raise `PoolConfigError` before connecting. Services with no database never call this function.
- Otherwise `await asyncpg.create_pool(dsn=settings.database_url, min_size=settings.db_pool_min, max_size=settings.db_pool_max)`.
- Return that pool. The caller stores it and later calls `await pool.close()`.

`acquire`:

- `async with pool.acquire(timeout=settings.db_acquire_timeout_ms / 1000) as conn`.
- The timeout argument is seconds. 1000 ms becomes `1.0`.
- Yield `conn`.
- On timeout, asyncpg raises `TimeoutError`. Let it propagate. `readyz` turns it into a failed `"db"` check.

`ping_db`:

- Acquire a connection as above and `await conn.execute("SELECT 1")`.

`db_readiness_check`:

- `get_pool` is called when `/readyz` runs, not when the router is created.
- Return `ReadinessCheck(name="db", check=...)`.
- When `get_pool()` returns `None`, the check raises `RuntimeError("database pool is not open")`.
- When `get_pool()` returns a pool, the check calls `ping_db` with that pool.

This module does not install signal handlers and does not start a server.

### How a service opens the pool

This wiring lives in the service, not in `sandbox_common`. Copy it into `auth`. A stopped Postgres leaves the process running: `/healthz` returns 200 and `/readyz` returns 503.

The readiness check runs on the request, which is after startup. Keep the pool on a small holder created at import time, and point `db_readiness_check` at that holder so the check sees the pool the lifespan opened.

```python
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI

from sandbox_common import (
    configure_logging,
    create_pool,
    db_readiness_check,
    get_logger,
    get_settings,
    health_router,
)

settings = get_settings()
configure_logging(settings)
log = get_logger()


class Resources:
    pool: asyncpg.Pool | None = None


resources = Resources()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        resources.pool = await create_pool(settings)
    except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
        resources.pool = None
        log.warning("db_pool_unavailable", error_type=type(exc).__name__)
    yield
    if resources.pool is not None:
        await resources.pool.close()
        resources.pool = None


app = FastAPI(lifespan=lifespan)
app.include_router(
    health_router([db_readiness_check(lambda: resources.pool, settings)])
)
```

`db_readiness_check` takes a getter so the check reads the pool at request time. At import, `resources.pool` is still `None`. By the time `/readyz` runs, the lifespan has either stored a pool or left it as `None`.

Startup flow:

```mermaid
sequenceDiagram
    participant S as Service lifespan
    participant C as sandbox_common
    participant P as Postgres

    S->>C: get_settings()
    S->>C: configure_logging(settings)
    S->>C: create_pool(settings)
    alt Postgres accepts connections
        C->>P: open min_size connections
        C-->>S: pool
    else Postgres unreachable
        C-->>S: OSError or PostgresError
        S->>S: remember pool = None
    end
    Note over S: process is serving
    S-->>S: GET /healthz returns 200
    S->>C: readiness check
    alt pool is open and SELECT 1 works
        C-->>S: 200 ready
    else pool is missing or ping fails
        C-->>S: 503 not_ready
    end
```

### Tests — `tests/test_db.py`

Unit tests, no database:

1. `create_pool` with `database_url=None` raises `PoolConfigError` and does not attempt a connection.
2. `db_readiness_check(lambda: None, settings)` returns a check named `"db"`. Awaiting `check.check()` raises `RuntimeError`.

Integration tests, skipped unless `DATABASE_URL` is set in the environment (read it inside the test with `os.environ`, and build a `Settings` that uses it):

3. `create_pool` returns a pool, `ping_db` completes, `pool.close()` completes.
4. A FastAPI app whose only readiness check is `db_readiness_check(lambda: pool, settings)` returns 200 from `/readyz` while that pool is open.
5. After `await pool.close()`, point the getter at `None` (or at the closed pool) and call `/readyz` again. The response is 503 with `failed == ["db"]`. `/healthz` stays 200. A closed pool fails inside `ping_db`; a getter that returns `None` fails with `RuntimeError`. Either result is a correct 503.

Local Postgres for the integration tests, one container, no Compose file:

```bash
docker run --rm -d --name sandbox-pg \
  -e POSTGRES_PASSWORD=sandbox \
  -e POSTGRES_DB=sandbox \
  -p 5432:5432 \
  postgres:16

export DATABASE_URL=postgresql://postgres:sandbox@localhost:5432/sandbox
export SERVICE_NAME=sandbox_common
```

Wait until `docker exec sandbox-pg pg_isready -U postgres` prints accepting connections, then run pytest. The unit tests pass with `DATABASE_URL` unset. The integration tests run when it is set.

---

## Build order

1. Add `pyproject.toml`, the package directory, and an empty `__init__.py`. Install the package in editable mode.
2. Write `settings.py` and `test_settings.py`. Run pytest until those tests pass.
3. Write `logging.py` and `test_logging.py`.
4. Write `health.py` and `test_health.py`.
5. Write `db.py` and `test_db.py`. Run the integration tests against the local Postgres container.
6. Fill `__init__.py` with the exports listed above. Add a short test that imports every name in `__all__` from `sandbox_common`.

Run the whole file set after the last step:

```bash
.venv/bin/pytest packages/sandbox_common/tests
```

## Done

Move on to `auth` when all of these are true:

- `import sandbox_common` works in the project venv.
- Unit tests pass with no Postgres running.
- With `DATABASE_URL` pointed at the local Postgres, `create_pool` and `ping_db` succeed.
- A one-file FastAPI app that only mounts `health_router` can be started with uvicorn and curled:
  - `GET /healthz` returns 200.
  - `GET /readyz` returns 200 when the pool is open.
  - `GET /readyz` returns 503 when `DATABASE_URL` points at a closed port and the lifespan kept `pool` as `None`.
- Log lines from that process are JSON and include `service`.

Suggested smoke app, `packages/sandbox_common/smoke_app.py`. It is a manual check, not a service, and it is safe to delete once `auth` exists.

```python
import os
from contextlib import asynccontextmanager

import asyncpg
import uvicorn
from fastapi import FastAPI

from sandbox_common import (
    Settings,
    configure_logging,
    create_pool,
    db_readiness_check,
    get_logger,
    health_router,
)

settings = Settings(
    service_name=os.environ.get("SERVICE_NAME", "smoke"),
    database_url=os.environ.get("DATABASE_URL"),
)
configure_logging(settings)
log = get_logger()


class Resources:
    pool: asyncpg.Pool | None = None


resources = Resources()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        resources.pool = await create_pool(settings)
    except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
        resources.pool = None
        log.warning("db_pool_unavailable", error_type=type(exc).__name__)
    yield
    if resources.pool is not None:
        await resources.pool.close()
        resources.pool = None


app = FastAPI(lifespan=lifespan)
app.include_router(health_router([db_readiness_check(lambda: resources.pool, settings)]))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

Run it with the venv's Python so the editable install is visible:

```bash
SERVICE_NAME=smoke DATABASE_URL=postgresql://postgres:sandbox@localhost:5432/sandbox \
  .venv/bin/python packages/sandbox_common/smoke_app.py
```

Curl:

```bash
curl -sS -D - http://127.0.0.1:8000/healthz
curl -sS -D - http://127.0.0.1:8000/readyz
```

## Where the next services plug in

| Service | Calls `create_pool` | Readiness checks in this slice |
|---|---|---|
| auth | yes, `auth_db` | `db`, plus a Redis check written inside auth |
| inventory | yes, `inventory_db` | `db`, plus a Redis check written inside inventory |
| payments | yes, `payments_db` | `db` |
| orders | yes, `orders_db` | `db` |
| cart | no | Redis check written inside cart |
| provider-mock | no | none, so `/readyz` is 200 |
| gateway | no | none in this slice; later it may check that auth is reachable |

Redis, token routes, and migrations are service code. They show up first in `auth`.

## Later slices of this same package

Add these as new modules when the matching part of the sandbox is being built. Their absence is intentional in this slice.

| Module | When | Role |
|---|---|---|
| `http.py` | cart calls inventory | httpx client factory using per-dependency timeout, retry count, and backoff. |
| `telemetry.py` | the collector and Grafana are running | `setup(service_name)` for traces, metrics, and logs over OTLP. |
| `rng.py` | load and fault schedules | `rng(namespace)` derived from the run seed. |
| `chaos.py` | the chaos injector | `/_chaos/*`, mounted only when `SANDBOX_CHAOS_HOOKS=1`. |

Shared settings grow at that time with the matching variables (`HTTP_TIMEOUT_MS`, the OTLP endpoint, the run seed). Existing fields stay as specified above.
