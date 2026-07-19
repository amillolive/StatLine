from __future__ import annotations

from statline.manifest.statpack.definitions import STATPACK_FILE_TYPE


def render_statpack_mime_xml() -> str:
    manifest = STATPACK_FILE_TYPE

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="{manifest.mime_type}">
    <comment>{manifest.friendly_name}</comment>
    <glob pattern="*{manifest.extension}"/>
  </mime-type>
</mime-info>
"""


def render_statline_desktop_entry(
    *,
    executable: str = "statline",
    icon: str = "statline",
) -> str:
    manifest = STATPACK_FILE_TYPE

    return f"""[Desktop Entry]
Type=Application
Name=StatLine
Comment=Open {manifest.friendly_name} files
Exec={executable} %f
Icon={icon}
Terminal=false
MimeType={manifest.mime_type};
Categories=Utility;
"""
