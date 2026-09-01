from __future__ import annotations

from pathlib import Path

from statline.manifest.definitions.windows import WindowsFileAssociation
from statline.manifest.functions.windows import (
    register_windows_file_type,
    unregister_windows_file_type,
)
from statline.manifest.statpack.definitions import STATPACK_FILE_TYPE


def _statpack_icon_path() -> Path:
    package_root = Path(__file__).resolve().parents[2]
    packaged_icon = package_root / "assets" / STATPACK_FILE_TYPE.icon_name
    if packaged_icon.is_file():
        return packaged_icon

    source_icon = package_root.parent / "assets" / STATPACK_FILE_TYPE.icon_name
    if source_icon.is_file():
        return source_icon

    raise FileNotFoundError(f"StatPack icon was not found: {STATPACK_FILE_TYPE.icon_name}")


def register_statpack_file_type(
    executable: str | Path,
) -> None:
    """
    Register `.statpack` as a StatLine file type for the current user.

    Explorer launches ``statline statpack run --pause`` so a double-clicked package
    keeps its temporary console open after the runner succeeds or fails.
    Normal terminal and automation calls remain non-interactive.
    """
    association = WindowsFileAssociation(
        manifest=STATPACK_FILE_TYPE,
        executable=Path(executable),
        arguments=("statpack", "run", "--pause"),
        icon_path=_statpack_icon_path(),
    )

    register_windows_file_type(association)


def unregister_statpack_file_type() -> None:
    """Remove the current-user `.statpack` Windows association."""
    unregister_windows_file_type(STATPACK_FILE_TYPE)
