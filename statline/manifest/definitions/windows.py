from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from statline.manifest.definitions.file_type import FileTypeManifest


class FileAssociationError(RuntimeError):
    """Raised when an operating-system file association cannot be changed."""


@dataclass(frozen=True, slots=True)
class WindowsFileAssociation:
    manifest: FileTypeManifest
    executable: Path
    arguments: tuple[str, ...] = ()

    @property
    def command(self) -> str:
        executable = str(self.executable.resolve())
        arguments = " ".join(
            f'"{argument}"' if " " in argument else argument for argument in self.arguments
        )

        if arguments:
            return f'"{executable}" {arguments} "%1"'

        return f'"{executable}" "%1"'

    @property
    def icon(self) -> str:
        return f"{self.executable.resolve()},0"
