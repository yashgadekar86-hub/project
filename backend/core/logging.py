"""
Structured logging.

Each log record carries: timestamp, service, event, severity, symbol, ticket,
user, message, metadata — per spec section 39.
"""
from __future__ import annotations

import logging
import sys
from typing import Any, Dict, Optional

from pythonjsonlogger import jsonlogger

from backend.core.config import settings


_AUDIT_LOGGER_NAME = "aifx.audit"
_SYS_LOGGER_NAME = "aifx.system"


class ContextFilter(logging.Filter):
    def __init__(self, service: str = "backend") -> None:
        super().__init__()
        self.service = service

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = getattr(record, "service", self.service)
        record.event = getattr(record, "event", record.name)
        record.symbol_name = getattr(record, "symbol_name", None)
        record.ticket = getattr(record, "ticket", None)
        record.user_id = getattr(record, "user_id", None)
        record.metadata_json = getattr(record, "metadata", {})
        return True


def configure_logging() -> None:
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    root = logging.getLogger()
    # Avoid duplicate handlers if reconfigured
    if getattr(root, "_aifx_configured", False):
        root.setLevel(level)
        return

    root.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    if settings.LOG_FORMAT == "json":
        fmt = jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(service)s %(event)s %(name)s "
            "%(message)s %(symbol_name)s %(ticket)s %(user_id)s %(metadata_json)s",
            rename_fields={
                "asctime": "timestamp",
                "levelname": "severity",
                "name": "logger",
                "metadata_json": "metadata",
            },
            timestamp=True,
        )
    else:
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(service)s | %(event)s | %(message)s"
        )

    handler.setFormatter(fmt)
    handler.addFilter(ContextFilter())
    root.addHandler(handler)
    root._aifx_configured = True  # type: ignore[attr-defined]

    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "websockets", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str = _SYS_LOGGER_NAME) -> logging.Logger:
    return logging.getLogger(name)


def get_audit_logger() -> logging.Logger:
    return logging.getLogger(_AUDIT_LOGGER_NAME)


def log_event(
    service: str,
    event: str,
    message: str,
    severity: str = "INFO",
    symbol: Optional[str] = None,
    ticket: Optional[int] = None,
    user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit a structured event log."""
    logger = get_logger(f"aifx.{service}")
    lvl = getattr(logging, severity.upper(), logging.INFO)
    logger.log(
        lvl,
        message,
        extra={
            "service": service,
            "event": event,
            "symbol_name": symbol,
            "ticket": ticket,
            "user_id": user_id,
            "metadata": metadata or {},
        },
    )


# Safety / system event helpers
def log_risk_event(event: str, message: str, **kw: Any) -> None:
    log_event("risk", event, message, severity=kw.pop("severity", "WARNING"), **kw)


def log_trade_event(event: str, message: str, **kw: Any) -> None:
    log_event("execution", event, message, severity=kw.pop("severity", "INFO"), **kw)


def log_ai_event(event: str, message: str, **kw: Any) -> None:
    log_event("ai", event, message, severity=kw.pop("severity", "INFO"), **kw)
