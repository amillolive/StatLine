from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FileTypeManifest:
    """
    Operating-system metadata for a custom StatLine file type.

    This object describes how an operating system should identify and
    associate a file extension. It does not define the file's contents.
    """

    extension: str
    prog_id: str
    friendly_name: str
    description: str
    mime_type: str
    uniform_type_identifier: str
    icon_name: str

    def __post_init__(self) -> None:
        if not self.extension.startswith("."):
            raise ValueError("File extensions must begin with a period.")

        if len(self.extension) < 2:
            raise ValueError("A file extension cannot be empty.")

        if not self.prog_id:
            raise ValueError("A Windows ProgID is required.")

        if "/" not in self.mime_type:
            raise ValueError("MIME types must use the type/subtype form.")

    def windows_icon(self, executable: Path) -> str:
        """Return the Windows icon resource reference."""
        return f"{executable.resolve()},0"