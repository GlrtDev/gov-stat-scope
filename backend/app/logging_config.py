"""Structured JSON logging configuration with request and trace context propagation."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.context import get_request_id, get_trace_id

LOGGER_NAME = "govdata.orchestrator"
_ID_PLACEHOLDER = "-"


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects including context IDs and extra attributes."""

    def format(self, record: logging.LogRecord) -> str:
        log_payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id() or getattr(record, "request_id", _ID_PLACEHOLDER),
            "trace_id": get_trace_id() or getattr(record, "trace_id", _ID_PLACEHOLDER),
        }

        # Merge additional custom fields passed via extra={}
        standard_attrs = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "request_id", "trace_id"
        }
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                log_payload[key] = value

        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_payload)


class RequestIdFilter(logging.Filter):
    """Ensures request_id and trace_id attributes exist on records for fallback formatters."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or _ID_PLACEHOLDER
        record.trace_id = get_trace_id() or _ID_PLACEHOLDER
        return True


def get_logger() -> logging.Logger:
    """Return the service-scoped logger."""
    return logging.getLogger(LOGGER_NAME)


def configure_logging() -> None:
    """Configure structured JSON logging for the service idempotently."""
    logger = get_logger()
    if logger.handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addFilter(RequestIdFilter())