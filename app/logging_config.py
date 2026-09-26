"""
Structured logging with PII redaction.
Production logs MUST NOT contain: full names, phone numbers, addresses,
complaint text, or file URLs. Only IDs and metadata are logged.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any


# PII patterns to redact in logs
PII_PATTERNS = [
    # Phone numbers (+998...)
    (re.compile(r"\+998\d{9}"), "[PHONE_REDACTED]"),
    # Telegram file URLs
    (re.compile(r"https?://api\.telegram\.org/file/[^\s]+"), "[FILE_URL_REDACTED]"),
]

# Fields that should never appear in structured log data
PII_FIELD_NAMES = frozenset({
    "full_name", "phone", "phone_number", "address", "manzil",
    "complaint_text", "murojaat_matni", "description", "response_text",
    "file_url", "file_path",
})


class PIIRedactingFormatter(logging.Formatter):
    """Custom formatter that redacts PII from log messages."""

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        for pattern, replacement in PII_PATTERNS:
            message = pattern.sub(replacement, message)
        return message


class StructuredLogger:
    """Logger wrapper that automatically redacts PII from structured data."""

    def __init__(self, name: str) -> None:
        self.logger = logging.getLogger(name)

    def _sanitize_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Remove or redact PII fields from log extra data."""
        sanitized = {}
        for key, value in kwargs.items():
            if key.lower() in PII_FIELD_NAMES:
                sanitized[key] = "[PII_REDACTED]"
            else:
                sanitized[key] = value
        return sanitized

    def info(self, msg: str, **kwargs: Any) -> None:
        safe = self._sanitize_kwargs(kwargs)
        self.logger.info(msg, extra={"data": safe} if safe else {})

    def warning(self, msg: str, **kwargs: Any) -> None:
        safe = self._sanitize_kwargs(kwargs)
        self.logger.warning(msg, extra={"data": safe} if safe else {})

    def error(self, msg: str, **kwargs: Any) -> None:
        safe = self._sanitize_kwargs(kwargs)
        self.logger.error(msg, extra={"data": safe} if safe else {})

    def debug(self, msg: str, **kwargs: Any) -> None:
        safe = self._sanitize_kwargs(kwargs)
        self.logger.debug(msg, extra={"data": safe} if safe else {})

    def exception(self, msg: str, **kwargs: Any) -> None:
        safe = self._sanitize_kwargs(kwargs)
        self.logger.exception(msg, extra={"data": safe} if safe else {})


def setup_logging(level: str = "INFO") -> None:
    """Configure application logging with PII redaction."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler with PII redaction
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(PIIRedactingFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    ))
    root_logger.addHandler(handler)

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> StructuredLogger:
    """Get a structured logger with PII redaction."""
    return StructuredLogger(name)
