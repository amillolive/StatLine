# statline/slapi/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SlapiConfig:
    """
    Central configuration for SLAPI.
    Environment variables override defaults.
    """

    # API metadata
    title: str = "StatLine API"
    version: str = "0.1.0"

    # Networking
    host: str = "127.0.0.1"
    port: int = 8000

    # Security / auth (optional, future use)
    api_key: Optional[str] = None

    # Debugging
    debug: bool = False


def load_config() -> SlapiConfig:
    """Load config from environment variables, with safe parsing."""
    return SlapiConfig(
        title=os.getenv("SLAPI_TITLE", "StatLine API"),
        version=os.getenv("SLAPI_VERSION", "0.1.0"),
        host=os.getenv("SLAPI_HOST", "127.0.0.1"),
        port=_parse_int(os.getenv("SLAPI_PORT"), 8000),
        api_key=os.getenv("SLAPI_KEY"),
        debug=_parse_bool(os.getenv("SLAPI_DEBUG"), False),
    )


def _parse_int(val: str | None, default: int) -> int:
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _parse_bool(val: str | None, default: bool) -> bool:
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


# Singleton accessor (lazy-loaded)
_config: Optional[SlapiConfig] = None


def get_config() -> SlapiConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


__all__ = ["SlapiConfig", "load_config", "get_config"]
