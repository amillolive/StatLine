"""Release metadata functions."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

from ._release_types import ReleaseInfo


@lru_cache(maxsize=1)
def get_release_info() -> ReleaseInfo:
    text = files("statline").joinpath("RELEASE").read_text(encoding="utf-8")

    values: dict[str, int] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        key, separator, value = line.partition("=")

        if not separator:
            raise ValueError(f"Invalid RELEASE entry: {line!r}")

        values[key.strip()] = int(value.strip())

    required = {
        "GENERATION",
        "CORE_RELEASE",
        "GATEWAY_RELEASE",
        "APP_RELEASE",
    }

    missing = required - values.keys()
    if missing:
        raise ValueError(f"Missing RELEASE entries: {', '.join(sorted(missing))}")

    return ReleaseInfo(
        generation=values["GENERATION"],
        core=values["CORE_RELEASE"],
        gateway=values["GATEWAY_RELEASE"],
        app=values["APP_RELEASE"],
    )


RELEASE: ReleaseInfo = get_release_info()
