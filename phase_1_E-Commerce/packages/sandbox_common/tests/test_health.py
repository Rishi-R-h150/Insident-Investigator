import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import CaptureFixture

from sandbox_common.health import ReadinessCheck, health_router
from sandbox_common.logging import configure_logging
from sandbox_common.settings import Settings


def _client(*checks: ReadinessCheck) -> TestClient:
    app = FastAPI()
    app.include_router(health_router(checks))
    return TestClient(app)


async def _ok() -> None:
    return None


async def _secret() -> None:
    raise RuntimeError("password=secret")


async def _fail_db() -> None:
    raise RuntimeError("db down")


async def _fail_redis() -> None:
    raise TimeoutError("redis down")


def test_no_checks() -> None:
    client = _client()
    health = client.get("/healthz")
    ready = client.get("/readyz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


def test_passing_check() -> None:
    response = _client(ReadinessCheck("db", _ok)).get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_failed_check_hides_message() -> None:
    response = _client(ReadinessCheck("db", _secret)).get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "failed": ["db"]}
    assert "secret" not in response.text


def test_failed_names_stay_in_registration_order() -> None:
    response = _client(
        ReadinessCheck("db", _fail_db),
        ReadinessCheck("redis", _fail_redis),
    ).get("/readyz")
    assert response.status_code == 503
    assert response.json()["failed"] == ["db", "redis"]


def test_healthz_ignores_failed_check() -> None:
    response = _client(ReadinessCheck("db", _secret)).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_failure_is_logged(capsys: CaptureFixture[str]) -> None:
    configure_logging(Settings(service_name="auth", log_level="INFO"))
    response = _client(ReadinessCheck("db", _secret)).get("/readyz")
    assert response.status_code == 503
    captured = capsys.readouterr().out
    matched = [
        json.loads(line)
        for line in captured.splitlines()
        if line.strip()
    ]
    events = [line for line in matched if line.get("event") == "readiness_check_failed"]
    assert len(events) == 1
    assert events[0]["check"] == "db"
    assert events[0]["error_type"] == "RuntimeError"
