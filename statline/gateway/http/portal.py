"""Optional browser-origin configuration for the existing Gateway."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from statline.gateway.auth.service import HDR_DEVICE_ID, HDR_NONCE, HDR_SIGNATURE, HDR_TIMESTAMP


def configure_portal_cors(app: FastAPI) -> None:
    """Allow explicit portal origins without changing authentication or adding routes."""
    origins = list(
        dict.fromkeys(
            [
                "https://statline.dev",
                "https://www.statline.dev",
                *[
                    value.strip().rstrip("/")
                    for value in os.getenv("STATLINE_PORTAL_ORIGINS", "").split(",")
                    if value.strip()
                ],
            ]
        )
    )
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or "*" in origin
        ):
            raise ValueError("STATLINE_PORTAL_ORIGINS requires exact HTTP(S) origins")
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                HDR_DEVICE_ID,
                HDR_TIMESTAMP,
                HDR_NONCE,
                HDR_SIGNATURE,
            ],
            expose_headers=["Server-Timing"],
        )


__all__ = ["configure_portal_cors"]
