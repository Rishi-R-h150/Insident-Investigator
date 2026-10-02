import structlog

from sandbox_common.settings import Settings


def configure_logging(settings: Settings) -> None:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(service=settings.service_name)

    minimum = structlog.stdlib.NAME_TO_LEVEL[settings.log_level.lower()]

    def drop_below(logger, method_name, event_dict):
        current_level = structlog.stdlib.NAME_TO_LEVEL[method_name]
        if current_level < minimum:
            raise structlog.DropEvent
        return event_dict

    structlog.configure(
        processors=[
            drop_below,
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger() -> structlog.stdlib.BoundLogger:
    return structlog.get_logger()
