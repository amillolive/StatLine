from __future__ import annotations

import os
from pathlib import Path


def project_caps_dir(cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    return base / ".statline" / "caps"

def user_cache_caps_dir() -> Path:
    xdg = os.getenv("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "statline" / "caps"

def resolve_caps_read_path(adapter_key: str, explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.exists() else None
    p1 = project_caps_dir() / f"{adapter_key}.csv"
    if p1.exists():
        return p1
    p2 = user_cache_caps_dir() / f"{adapter_key}.csv"
    if p2.exists():
        return p2
    return None

def resolve_caps_write_path(adapter_key: str, prefer_project: bool = True) -> Path:
    target_dir = project_caps_dir() if prefer_project else user_cache_caps_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / f"{adapter_key}.csv"
