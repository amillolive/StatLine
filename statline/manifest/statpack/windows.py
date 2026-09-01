from __future__ import annotations

from pathlib import Path

from statline.manifest.definitions.windows import WindowsFileAssociation
from statline.manifest.functions.windows import (
    register_windows_file_type,
    unregister_windows_file_type,
)
from statline.manifest.statpack.definitions import STATPACK_FILE_TYPE


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
    )

    register_windows_file_type(association)


def unregister_statpack_file_type() -> None:
    """Remove the current-user `.statpack` Windows association."""
    unregister_windows_file_type(STATPACK_FILE_TYPE)
