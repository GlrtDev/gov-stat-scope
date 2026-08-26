"""Structured (key=value) logging setup for the orchestrator service."""

from __future__ import annotations

import logging

from app.context import get_request_id, get_trace_id

LOGGER_NAME = "govdata.orchestrator"

# Placeholder used in log records when no request is active.
_ID_PLACEHOLDER = "-"


class RequestIdFilter(logging.Filter):
    """Inject the current request/trace IDs into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or _ID_PLACEHOLDER
        record.trace_id = get_trace_id() or _ID_PLACEHOLDER
        return True


def get_logger() -> logging.Logger:
    """Return the service-scoped logger."""
    return logging.getLogger(LOGGER_NAME)


def configure_logging() -> None:
    """Configure structured (key=value) logging for the service.

    Idempotent: returns immediately if the logger already has handlers.
    """
    logger = get_logger()
    if logger.handlers:
        return

    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt=(
            "level=%(levelname)s "
            "logger=%(name)s "
            "request_id=%(request_id)s "
            "trace_id=%(trace_id)s "
            "message=%(message)s"
        )
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.addFilter(RequestIdFilter())
