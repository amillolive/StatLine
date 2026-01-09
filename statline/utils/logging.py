from __future__ import annotations

import logging
import os
import sys
from typing import Optional, TextIO

# Env knobs:
#   STATLINE_LOG_LEVEL: DEBUG|INFO|WARNING|ERROR|CRITICAL  (default: INFO)
#   STATLINE_LOG_FORMAT: "plain" (default) | "verbose"
#
# Example:
#   STATLINE_LOG_LEVEL=DEBUG python -m statline

def _parse_level(value: str | None) -> int:
    if not value:
        return logging.INFO
    value = value.upper().strip()
    return getattr(logging, value, logging.INFO)


def _make_formatter(fmt_style: str) -> logging.Formatter:
    if fmt_style == "verbose":
        fmt = "%(asctime)s [%(levelname)s] %(name)s (%(process)d:%(threadName)s): %(message)s"
        datefmt = "%Y-%m-%dT%H:%M:%S%z"
    else:  # "plain"
        fmt = "[%(levelname)s] %(name)s: %(message)s"
        datefmt = None
    return logging.Formatter(fmt=fmt, datefmt=datefmt)


def get_logger(
    name: str = "statline",
    *,
    stream: Optional[TextIO] = None,
) -> logging.Logger:
    """
    Central logger factory for Statline.

    - Respects STATLINE_LOG_LEVEL (default INFO) and STATLINE_LOG_FORMAT.
    - Adds a single StreamHandler if none exist.
    - Sets logger.propagate = False to avoid duplicate logs through the root logger.
    """
    logger = logging.getLogger(name)

    # Level from env, default INFO
    level = _parse_level(os.getenv("STATLINE_LOG_LEVEL"))
    logger.setLevel(level)

    # Avoid double logging through ancestors (root handlers)
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler(stream or sys.stderr)
        fmt_style = os.getenv("STATLINE_LOG_FORMAT", "plain").lower()
        handler.setFormatter(_make_formatter(fmt_style))
        # Handler should not be chattier than the logger itself
        handler.setLevel(level)
        logger.addHandler(handler)

    return logger
