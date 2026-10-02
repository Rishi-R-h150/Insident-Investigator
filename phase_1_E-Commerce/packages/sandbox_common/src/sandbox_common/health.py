from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from sandbox_common.logging import get_logger


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    check: Callable[[], Awaitable[None]]


def health_router(checks: Sequence[ReadinessCheck]) -> APIRouter:
    registered = tuple(checks)
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/readyz", response_model=None)
    async def readyz() -> JSONResponse | dict[str, object]:
        failed: list[str] = []
        log = get_logger()
        for readiness in registered:
            try:
                await readiness.check()
            except Exception as exc:
                failed.append(readiness.name)
                log.warning(
                    "readiness_check_failed",
                    check=readiness.name,
                    error_type=type(exc).__name__,
                )
        if failed:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "failed": failed},
            )
        return {"status": "ready"}

    return router
