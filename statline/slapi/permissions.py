# statline/slapi/permissions.py
from __future__ import annotations

from typing import Iterable, Set

SCOPE_USERBASE = "userbase"
SCOPE_MODERATION = "moderation"
SCOPE_ADMIN = "admin"

KNOWN_SCOPES: Set[str] = {SCOPE_USERBASE, SCOPE_MODERATION, SCOPE_ADMIN}

# Scope implication: admin > moderation > userbase
IMPLIES = { # pyright: ignore[reportUnknownVariableType]
    SCOPE_ADMIN: {SCOPE_MODERATION, SCOPE_USERBASE},
    SCOPE_MODERATION: {SCOPE_USERBASE},
    SCOPE_USERBASE: set(),
}

def expand_scopes(scopes: Iterable[str]) -> Set[str]:
    out = set(scopes)
    changed = True
    while changed:
        changed = False
        for s in list(out):
            for implied in IMPLIES.get(s, set()): # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                if implied not in out:
                    out.add(implied) # pyright: ignore[reportUnknownArgumentType]
                    changed = True
    return out

def validate_scopes(scopes: Iterable[str]) -> Set[str]:
    s = set(scopes)
    unknown = s - KNOWN_SCOPES
    if unknown:
        raise ValueError(f"Unknown scopes: {sorted(unknown)}")
    return s
