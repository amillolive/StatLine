# statline/core/adapters/sniff.py
from __future__ import annotations

from typing import Iterable, List

from . import registry
from .hooks import get as get_hooks


def sniff_adapters(headers: Iterable[str]) -> List[str]:
    """
    Return adapter keys that "match" a set of headers.

    Matching rules (in order):
      1) hooks.sniff(headers) if the adapter has a hooks object implementing sniff
      2) YAML spec metadata: adapter.sniff.require_any_headers intersects headers
    """
    lowered = [str(h).strip().lower() for h in headers if str(h).strip()]
    hset = set(lowered)
    if not hset:
        return []

    out: List[str] = []
    seen: set[str] = set()

    for name in registry.list_names():
        adp = registry.load(name)

        # (1) hook-based sniff (optional)
        try:
            hooks = get_hooks(adp.key)
            sniff_fn = getattr(hooks, "sniff", None)
            if callable(sniff_fn) and bool(sniff_fn(lowered)):
                k = adp.key
                lk = k.lower()
                if lk not in seen:
                    seen.add(lk)
                    out.append(k)
                continue
        except Exception:
            # Sniff hooks must never be allowed to break adapter selection
            pass

        # (2) YAML metadata sniff
        sniff_meta = getattr(adp, "sniff", None)
        if isinstance(sniff_meta, dict):
            req_any = sniff_meta.get("require_any_headers") # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            if isinstance(req_any, (list, tuple, set)):
                need = {str(x).strip().lower() for x in req_any if str(x).strip()} # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
                if need and (need & hset):
                    k = adp.key
                    lk = k.lower()
                    if lk not in seen:
                        seen.add(lk)
                        out.append(k)

    return out


__all__ = ["sniff_adapters"]
