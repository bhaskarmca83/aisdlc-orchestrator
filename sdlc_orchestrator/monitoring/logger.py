"""Structured JSON logging — writes to logs/aisdlc-orchestrator.log for Promtail/Loki."""
import logging
import os
import sys
from pathlib import Path
from pythonjsonlogger import jsonlogger

APP_NAME = "aisdlc-orchestrator"
ENV      = os.environ.get("ENV", "local")
LOG_FILE = Path("logs/aisdlc-orchestrator.log")

_configured = False


class _StructuredFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["app"] = APP_NAME
        log_record["env"] = ENV
        log_record.pop("color_message", None)
        log_record.pop("taskName", None)


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    LOG_FILE.parent.mkdir(exist_ok=True)

    json_fmt = _StructuredFormatter(
        fmt="%(timestamp)s %(level)s %(name)s %(message)s",
        rename_fields={"levelname": "level", "asctime": "timestamp"},
        timestamp=True,
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # File handler — Promtail reads this as JSON lines
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(json_fmt)
    root.addHandler(fh)

    # Suppress noisy third-party loggers
    for noisy in ("httpx", "httpcore", "uvicorn.access", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
