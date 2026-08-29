from __future__ import annotations

from typing import Any

from statline.manifest.statpack.definitions import STATPACK_FILE_TYPE


def build_statpack_document_type() -> dict[str, Any]:
    manifest = STATPACK_FILE_TYPE

    return {
        "CFBundleTypeName": manifest.friendly_name,
        "CFBundleTypeRole": "Viewer",
        "LSHandlerRank": "Owner",
        "LSItemContentTypes": [
            manifest.uniform_type_identifier,
        ],
        "CFBundleTypeExtensions": [
            manifest.extension.removeprefix("."),
        ],
        "CFBundleTypeMIMETypes": [
            manifest.mime_type,
        ],
        "CFBundleTypeIconFile": manifest.icon_name,
    }


def build_statpack_exported_type() -> dict[str, Any]:
    manifest = STATPACK_FILE_TYPE

    return {
        "UTTypeIdentifier": manifest.uniform_type_identifier,
        "UTTypeDescription": manifest.description,
        "UTTypeConformsTo": [
            "public.data",
        ],
        "UTTypeTagSpecification": {
            "public.filename-extension": [
                manifest.extension.removeprefix("."),
            ],
            "public.mime-type": [
                manifest.mime_type,
            ],
        },
    }
