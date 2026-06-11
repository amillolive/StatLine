from __future__ import annotations

import platform

from statline.cli import (
    APIKEY_PATH,
    DEVICEID_PATH,
    KEYS_DIR,
    _device_pub_b64_from_priv,
    _ensure_ed25519_keypair,
)
from statline.slapi.auth import (
    DB_PATH,
    DEVKEY_PATH,
    admin_approve_enrollment,
    admin_generate_devkey_files,
    admin_mint_regtoken,
    create_api_key_for_device,
    create_enrollment_request,
)

ORG = "statline"
USER = "conner"
EMAIL = "conner.walston@valpo.edu"

# admin implies moderation + userbase server-side
SCOPES = ["admin"]


def main() -> None:
    print(f"Auth DB: {DB_PATH}")

    if not DEVKEY_PATH.exists():
        print("DEVKEY missing. Generating DEVKEY...")
        info = admin_generate_devkey_files(overwrite=False)
        print(info)
    else:
        print(f"DEVKEY present: {DEVKEY_PATH}")

    # Ensure this machine has a DEVICEKEY.
    priv = _ensure_ed25519_keypair(force=False)
    device_pub_b64 = _device_pub_b64_from_priv(priv)

    # Mint a local one-time regtoken directly, bypassing admin HTTP.
    regtoken = admin_mint_regtoken(
        org=ORG,
        scopes=SCOPES,
        ttl_days=None,
    )

    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    regtoken_path = KEYS_DIR / "bootstrap.regt"
    regtoken_path.write_text(regtoken, encoding="utf-8")

    print(f"Minted local bootstrap regtoken: {regtoken_path}")

    # Redeem the token into a pending enrollment.
    enrollment = create_enrollment_request(
        reg_token=regtoken,
        user=USER,
        email=EMAIL,
        device_pub_b64=device_pub_b64,
        meta={
            "hostname": platform.node(),
            "os": platform.platform(),
            "cli_version": "local-bootstrap",
        },
    )

    request_id = enrollment["request_id"]
    device_id = enrollment["device_id"]

    print(f"Created enrollment request: {request_id}")
    print(f"Device ID: {device_id}")

    # Approve that enrollment directly.
    ok = admin_approve_enrollment(
        request_id=request_id,
        decided_by="local-bootstrap",
        decision_note="Bootstrap first local admin device",
    )

    if not ok:
        raise SystemExit("Failed to approve enrollment.")

    # Mint an admin API key directly for the approved device.
    api_token, record = create_api_key_for_device(
        device_id=device_id,
        owner=USER,
        scopes=SCOPES,
        ttl_days=3650,
    )

    DEVICEID_PATH.write_text(device_id, encoding="utf-8")
    APIKEY_PATH.write_text(api_token, encoding="utf-8")

    print("")
    print("Local admin bootstrap complete.")
    print(f"DEVICEID written: {DEVICEID_PATH}")
    print(f"APIKEY written:   {APIKEY_PATH}")
    print(f"API prefix:       {record['prefix']}")
    print("")
    print("Next:")
    print("  statline --mode auto auth status")
    print("  statline --mode auto auth whoami")


if __name__ == "__main__":
    main()