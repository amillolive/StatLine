from __future__ import annotations

from statline.manifest.definitions.file_type import FileTypeManifest

STATPACK_FILE_TYPE = FileTypeManifest(
    extension=".statpack",
    prog_id="StatLine.StatPack",
    friendly_name="StatLine StatPack",
    description="StatLine scoring package",
    mime_type="application/vnd.statline.statpack",
    uniform_type_identifier="dev.statline.statpack",
    icon_name="statpack.ico",
)