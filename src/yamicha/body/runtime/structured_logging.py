"""Correlation-aware JSON logging for technical observations."""

from __future__ import annotations

import json
import logging
from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Any, TextIO


class JsonFormatter(logging.Formatter):
    """Render one technical observation as one JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None),
        }
        event = getattr(record, "event", None)
        if event is not None:
            payload["event"] = event
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class CorrelationLoggerAdapter(logging.LoggerAdapter[logging.Logger]):
    """Attach one lifecycle correlation ID without hiding call-specific fields."""

    def process(
        self,
        msg: object,
        kwargs: MutableMapping[str, Any],
    ) -> tuple[object, MutableMapping[str, Any]]:
        supplied = kwargs.get("extra", {})
        kwargs["extra"] = {**supplied, **self.extra}
        return msg, kwargs


def configure_json_logger(
    name: str = "yamicha",
    *,
    stream: TextIO | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Create an isolated logger that emits UTF-8-safe JSON lines."""

    logger = logging.getLogger(name)
    logger.handlers.clear()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def bind_correlation(
    logger: logging.Logger,
    correlation_id: str,
) -> CorrelationLoggerAdapter:
    """Bind a required correlation ID to subsequent log records."""

    if not correlation_id.strip():
        raise ValueError("correlation_id must not be empty")
    return CorrelationLoggerAdapter(logger, {"correlation_id": correlation_id})
