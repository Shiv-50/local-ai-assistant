"""
Centralised structured logging for local-ai-assistant.

Usage:
    from src.utils.logger import get_logger
    log = get_logger(__name__)
    log.info("model loaded", model="qwen2.5:7b", elapsed_ms=320)
"""

import logging
import logging.handlers
import time
import json
import sys


# ─────────────────────────────────────────────
# JSON formatter  (machine-readable log lines)
# ─────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line."""

    _SKIP = frozenset({
        "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno",
        "funcName", "created", "msecs", "relativeCreated", "thread",
        "threadName", "processName", "process", "name", "message",
        "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts":     self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k not in self._SKIP:
                payload[k] = v
        return json.dumps(payload, default=str)


# ─────────────────────────────────────────────
# KwargsLogger — wraps a Logger so callers can
# pass arbitrary keyword args without boilerplate
# ─────────────────────────────────────────────

class KwargsLogger:
    """Thin wrapper that folds **kwargs into extra={} automatically.

    Usage:
        log = get_logger(__name__)
        log.info("done", elapsed_ms=42, model="qwen")
    """

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _log(self, level: int, msg: str, *args, **kwargs):
        exc_info  = kwargs.pop("exc_info", False)
        stack_info= kwargs.pop("stack_info", False)
        extra     = kwargs.pop("extra", {})
        extra.update(kwargs)          # remaining kwargs become log fields
        self._logger.log(
            level, msg, *args,
            exc_info=exc_info,
            stack_info=stack_info,
            extra=extra,
            stacklevel=2,
        )

    def debug(self, msg, *args, **kwargs):
        self._log(logging.DEBUG, msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        self._log(logging.INFO, msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self._log(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self._log(logging.ERROR, msg, *args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        kwargs.setdefault("exc_info", True)
        self._log(logging.ERROR, msg, *args, **kwargs)

    # pass-through for anything else (handlers, level checks, …)
    def __getattr__(self, name):
        return getattr(self._logger, name)


# ─────────────────────────────────────────────
# Timing helper
# ─────────────────────────────────────────────

class TimedBlock:
    """Context manager that logs elapsed time on exit.

    Example::

        with TimedBlock(log, "llm_invoke", model="qwen2.5:7b"):
            result = llm.invoke(messages)
    """

    def __init__(self, logger: KwargsLogger, operation: str, **extra):
        self._log   = logger
        self._op    = operation
        self._extra = extra
        self._start: float = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        self._log.debug("start", operation=self._op, **self._extra)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed_ms = round((time.perf_counter() - self._start) * 1000, 1)
        if exc_type:
            self._log.warning(
                "failed",
                operation=self._op,
                elapsed_ms=elapsed_ms,
                error=str(exc_val),
                **self._extra,
            )
        else:
            self._log.info(
                "done",
                operation=self._op,
                elapsed_ms=elapsed_ms,
                **self._extra,
            )
        return False  # never suppress exceptions


# ─────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────

def get_logger(name: str) -> KwargsLogger:
    """Return a KwargsLogger child under the 'assistant' hierarchy."""
    return KwargsLogger(logging.getLogger(f"assistant.{name}"))


# ─────────────────────────────────────────────
# One-time bootstrap  (called from main.py)
# ─────────────────────────────────────────────

def setup_logging(
    level: str = "INFO",
    log_file: str = "assistant.log",
    json_console: bool = False,
) -> None:
    """Configure root + assistant loggers."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # ── console handler ──────────────────────────────────────
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(numeric_level)
    if json_console:
        console.setFormatter(JsonFormatter())
    else:
        console.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    # ── rotating file handler (JSON, always) ─────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JsonFormatter())

    # ── root logger ───────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)

    # ── silence noisy third-party loggers ────────────────────
    for noisy in ("httpx", "httpcore", "urllib3", "playwright"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("assistant").info(
        f"Logging initialised | level={level} | log_file={log_file}"
    )