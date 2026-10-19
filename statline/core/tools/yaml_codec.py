"""Fast, safe YAML I/O for untrusted adapter and StatPack documents.

Use libyaml's C safe classes when installed, never the unsafe Loader/Dumper.
The pure-Python safe classes remain the fallback for minimal installations.
"""

from __future__ import annotations

from typing import Any, cast

import yaml

SAFE_LOADER: type[yaml.SafeLoader] = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
SAFE_DUMPER: type[yaml.SafeDumper] = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


def safe_load(stream: str | bytes) -> Any:
    """Deserialize YAML without allowing Python-object constructors."""
    return yaml.load(stream, Loader=SAFE_LOADER)


def safe_dump(data: Any, **options: Any) -> str:
    """Serialize YAML with the matching safe dumper (and consistent defaults)."""
    encoded = cast(object, yaml.dump(data, Dumper=SAFE_DUMPER, **options))
    if not isinstance(encoded, str):
        raise TypeError("Safe YAML dumper did not return a string")
    return encoded


__all__ = ["SAFE_LOADER", "SAFE_DUMPER", "safe_load", "safe_dump"]
