# slapi/adapters.py
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union, cast

# Try to import PyYAML, but keep types simple so linters don’t complain.
try:  # pragma: no cover
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

AdapterConfig = Dict[str, Any]


# ──────────────────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────────────────

class YamlError(RuntimeError):
    """Raised on YAML loading/decoding problems."""


# ──────────────────────────────────────────────────────────────────────────────
# Result type
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ResolvedYaml:
    name: str          # logical name (no extension)
    path: Path         # real file path, or a virtual "<…>" path for inline
    config: AdapterConfig


# ──────────────────────────────────────────────────────────────────────────────
# Search paths
# ──────────────────────────────────────────────────────────────────────────────

def _split_paths(env_val: Optional[str]) -> List[Path]:
    if not env_val:
        return []
    parts = [p.strip() for p in env_val.split(os.pathsep) if p.strip()]
    return [Path(p).expanduser() for p in parts]

def _default_adapter_dirs() -> List[Path]:
    """
    Search order:
      1) SLAPI_ADAPTERS_DIR   (os.pathsep-separated)
      2) STATLINE_ADAPTERS_DIR (legacy)
      3) ./adapters
      4) ~/.config/statline/adapters
      5) ./config
    """
    out: List[Path] = []
    env = _split_paths(os.getenv("SLAPI_ADAPTERS_DIR")) or _split_paths(os.getenv("STATLINE_ADAPTERS_DIR"))
    out.extend(env)
    out.append(Path.cwd() / "adapters")
    out.append(Path.home() / ".config" / "statline" / "adapters")
    out.append(Path.cwd() / "config")

    # Deduplicate while preserving order
    seen: set[str] = set()
    ordered: List[Path] = []
    for p in out:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            ordered.append(p)
    return ordered

_ADAPTER_DIRS: List[Path] = _default_adapter_dirs()


# ──────────────────────────────────────────────────────────────────────────────
# YAML loading
# ──────────────────────────────────────────────────────────────────────────────

_yaml_cache: Dict[Path, AdapterConfig] = {}

def _yaml_load(text: str) -> AdapterConfig:
    if yaml is None:
        raise YamlError("PyYAML is not installed; cannot parse YAML.")
    try:
        obj = yaml.safe_load(text)
    except Exception as e:  # pragma: no cover
        raise YamlError(f"Failed to parse YAML: {e}") from e
    if obj is None:
        return {}
    if not isinstance(obj, dict):
        raise YamlError(f"YAML root must be a mapping/object, got {type(obj).__name__}")

    # Normalize to Dict[str, Any] with explicit loop var types (for Pylance/MyPy)
    obj_map: Mapping[Any, Any] = cast(Mapping[Any, Any], obj)
    result: Dict[str, Any] = {}
    for k, v in obj_map.items():
        result[str(k)] = v
    return result


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="utf-8", errors="ignore")


# ──────────────────────────────────────────────────────────────────────────────
# Resolution helpers
# ──────────────────────────────────────────────────────────────────────────────

def _candidate_paths(name: str) -> Iterable[Path]:
    stem = name
    if stem.endswith(".yml") or stem.endswith(".yaml"):
        for d in _ADAPTER_DIRS:
            yield d / stem
        return
    for d in _ADAPTER_DIRS:
        yield d / (stem + ".yml")
        yield d / (stem + ".yaml")

def find_yaml_adapter(name_or_path: Union[str, Path]) -> Path:
    p = Path(name_or_path).expanduser()
    if (p.suffix.lower() in (".yml", ".yaml")) and p.exists():
        return p
    for cand in _candidate_paths(str(name_or_path)):
        if cand.exists():
            return cand
    raise FileNotFoundError(f"YAML adapter not found: {name_or_path!s}")

def load_yaml_file(path: Union[str, Path]) -> AdapterConfig:
    p = Path(path).expanduser()
    cached = _yaml_cache.get(p)
    if cached is not None:
        return cached
    cfg = _yaml_load(_read_text(p))
    _yaml_cache[p] = cfg
    return cfg

def list_yaml_adapters() -> List[str]:
    names: set[str] = set()
    for d in _ADAPTER_DIRS:
        try:
            for entry in d.iterdir():
                if entry.is_file() and entry.suffix.lower() in (".yml", ".yaml"):
                    names.add(entry.stem)
        except Exception:
            continue
    return sorted(names)


# ──────────────────────────────────────────────────────────────────────────────
# Reference resolution: yaml:, yaml-file:, yaml-b64:
# ──────────────────────────────────────────────────────────────────────────────

def _decode_b64(s: str) -> str:
    try:
        raw = base64.b64decode(s, validate=True)
        return raw.decode("utf-8")
    except Exception as e:
        raise YamlError(f"Invalid base64 payload for yaml-b64: {e}") from e

def _virtual_path(tag: str) -> Path:
    return Path(f"<{tag}>")

def resolve_yaml_reference(ref: Union[str, Path]) -> ResolvedYaml:
    """
    Resolve a YAML adapter reference and return ResolvedYaml.
      - 'yaml:<name>'
      - 'yaml-file:<path>'
      - 'yaml-b64:<base64-utf8-yaml>'
    Also accepts raw filesystem paths ('/x/y.yml' or 'x.yml').
    """
    # Direct Path → treat as file
    if isinstance(ref, Path):
        p = find_yaml_adapter(ref)
        return ResolvedYaml(name=p.stem, path=p, config=load_yaml_file(p))

    s = str(ref).strip()

    # Raw filesystem path (without scheme)
    if s and (s.endswith(".yml") or s.endswith(".yaml")) and not s.startswith("yaml-"):
        p2 = find_yaml_adapter(s)
        return ResolvedYaml(name=p2.stem, path=p2, config=load_yaml_file(p2))

    if s.startswith("yaml-b64:"):
        payload = s[len("yaml-b64:"):].strip()
        text = _decode_b64(payload)
        cfg = _yaml_load(text)
        inferred = str(cfg.get("name") or cfg.get("adapter") or "inline")
        return ResolvedYaml(name=inferred, path=_virtual_path(f"yaml-b64:{inferred}"), config=cfg)

    if s.startswith("yaml-file:"):
        path_str = s[len("yaml-file:"):].strip()
        p3 = find_yaml_adapter(path_str)
        return ResolvedYaml(name=p3.stem, path=p3, config=load_yaml_file(p3))

    if s.startswith("yaml:"):
        name = s[len("yaml:"):].strip()
        p4 = find_yaml_adapter(name)
        return ResolvedYaml(name=p4.stem, path=p4, config=load_yaml_file(p4))

    # Fallback: treat as a logical name
    p5 = find_yaml_adapter(s)
    return ResolvedYaml(name=p5.stem, path=p5, config=load_yaml_file(p5))


# ──────────────────────────────────────────────────────────────────────────────
# Convenience
# ──────────────────────────────────────────────────────────────────────────────

def get_yaml_config(key: Union[str, Path]) -> AdapterConfig:
    return resolve_yaml_reference(key).config

def list_discoverable_yaml() -> List[str]:
    return list_yaml_adapters()


__all__ = [
    "AdapterConfig",
    "ResolvedYaml",
    "YamlError",
    "find_yaml_adapter",
    "load_yaml_file",
    "list_yaml_adapters",
    "resolve_yaml_reference",
    "get_yaml_config",
    "list_discoverable_yaml",
]
