"""
app/utils/logger.py
─────────────────────────────────────────────────────────────────────────────
Structured logging setup for SanjeevaniRxAI.

Design decisions
────────────────
• Single call to ``setup_logging()`` at application startup configures the
  root logger (and therefore all child loggers) consistently.
• Every log record includes: timestamp (ISO-8601 UTC), log level, module
  path, function name, line number, and message.
• In development (LOG_LEVEL=DEBUG) a colourised, human-friendly format is
  used.  In staging/production the handler emits newline-delimited JSON so
  that log-aggregators (Datadog, CloudWatch, Loki …) can parse records
  without custom parsing rules.
• ``get_logger(name)`` is the single factory used everywhere else:
      from app.utils.logger import get_logger
      logger = get_logger(__name__)

Usage
─────
    # In application startup (main.py):
    from app.utils.logger import setup_logging
    setup_logging()

    # In any other module:
    from app.utils.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Server started", extra={"port": 8000})
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# ANSI colour codes (terminal / development only)
# ──────────────────────────────────────────────────────────────────────────────
_RESET = "\033[0m"
_BOLD = "\033[1m"
_COLORS: dict[int, str] = {
    logging.DEBUG: "\033[36m",  # Cyan
    logging.INFO: "\033[32m",  # Green
    logging.WARNING: "\033[33m",  # Yellow
    logging.ERROR: "\033[31m",  # Red
    logging.CRITICAL: "\033[35m",  # Magenta
}


# ──────────────────────────────────────────────────────────────────────────────
# Formatters
# ──────────────────────────────────────────────────────────────────────────────


class _PrettyFormatter(logging.Formatter):
    """
    Human-readable, colourised formatter used in development.

    Output pattern (one line):
      2026-02-24T15:30:00.123Z  INFO      app.main          startup() L42  │ Server running on port 8000
    """

    FMT = (
        "{color}{bold}{asctime}{reset}  "
        "{color}{levelname:<9}{reset} "
        "{cyan}{name:<30}{reset}  "
        "{func}() L{lineno:<4}  {bold}│{reset} {message}"
    )

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        color = _COLORS.get(record.levelno, "")
        cyan = "\033[36m"
        record.asctime = (
            datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3]
            + "Z"
        )
        record.func = record.funcName

        line = self.FMT.format(
            color=color,
            bold=_BOLD,
            cyan=cyan,
            reset=_RESET,
            asctime=record.asctime,
            levelname=record.levelname,
            name=record.name,
            func=record.func,
            lineno=record.lineno,
            message=record.getMessage(),
        )

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)

        return line


class _JSONFormatter(logging.Formatter):
    """
    Structured JSON formatter used in staging/production.

    Each log line is a JSON object that includes:
      timestamp, level, logger, module, function, line, message
    plus any ``extra`` fields attached at the call-site.
    """

    # Fields that are part of LogRecord but should NOT be surfaced as extras
    _SKIP_FIELDS: frozenset[str] = frozenset(
        {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "id",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "taskName",
            "thread",
            "threadName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: dict[str, Any] = {
            "timestamp": (
                datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.%f"
                )[:-3]
                + "Z"
            ),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
        }

        # Bubble up any extra= kwargs passed by the caller
        for key, value in record.__dict__.items():
            if key not in self._SKIP_FIELDS:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────


def setup_logging(
    level: str | int | None = None,
    *,
    force_json: bool = False,
    force_pretty: bool = False,
) -> None:
    """
    Configure the root logger.  Call **once** at application startup.

    Parameters
    ----------
    level:
        Log level string / int (e.g. ``"DEBUG"``, ``logging.INFO``).
        Defaults to ``settings.LOG_LEVEL`` when *None*.
    force_json:
        Always use the JSON formatter regardless of ENV.
    force_pretty:
        Always use the pretty colourised formatter regardless of ENV.
    """
    # Lazy import to avoid circular dependencies at module load time
    from app.config import settings

    resolved_level: int | str = level if level is not None else settings.LOG_LEVEL

    # Choose formatter
    if force_json:
        formatter: logging.Formatter = _JSONFormatter()
    elif force_pretty:
        formatter = _PrettyFormatter()
    elif settings.is_production or settings.ENV == "staging":
        formatter = _JSONFormatter()
    else:
        formatter = _PrettyFormatter()

    # Stream handler → stdout (captured by Docker / systemd / gunicorn)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(resolved_level)
    handler.setFormatter(formatter)

    # Root logger
    root = logging.getLogger()
    root.setLevel(resolved_level)

    # Remove pre-existing handlers to avoid duplicate output
    root.handlers.clear()
    root.addHandler(handler)

    # Suppress overly verbose third-party loggers
    for noisy_lib in (
        "pymongo",
        "motor",
        "httpx",
        "httpcore",
        "uvicorn.access",
        "watchfiles",
    ):
        logging.getLogger(noisy_lib).setLevel(logging.WARNING)

    # Confirm setup (will show even at WARNING level in production)
    logging.getLogger(__name__).debug(
        "Logging initialised",
        extra={
            "level": logging.getLevelName(resolved_level),
            "env": settings.ENV,
            "formatter": type(formatter).__name__,
        },
    )


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger.

    Example
    -------
        logger = get_logger(__name__)
        logger.info("Order received", extra={"order_id": "ORD-001"})
    """
    return logging.getLogger(name)
