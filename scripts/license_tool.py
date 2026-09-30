"""Vendor tool: create your signing key once, then issue license keys to customers.

    # once - creates your PRIVATE key (keep it secret, never commit it) and writes the
    # public key into app/license_public_key.pem (commit this file)
    python scripts/license_tool.py keygen --private ~/attendance-license-private.pem

    # for each customer
    python scripts/license_tool.py issue --private ~/attendance-license-private.pem \
        --licensee "City College" --max-people 500 --expires 2027-12-31

    # check a key
    python scripts/license_tool.py verify KEY
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from app.services.license import PUBLIC_KEY_FILE, LicenseError, sign_license, verify_license  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--private", type=Path, required=True)
    k.add_argument("--force", action="store_true")
    i = sub.add_parser("issue")
    i.add_argument("--private", type=Path, required=True)
    i.add_argument("--licensee", required=True)
    i.add_argument("--max-people", type=int, default=None, help="omit for unlimited")
    i.add_argument("--expires", type=date.fromisoformat, default=None, help="YYYY-MM-DD, omit for perpetual")
    v = sub.add_parser("verify")
    v.add_argument("key")
    args = ap.parse_args()

    if args.cmd == "keygen":
        path = args.private.expanduser()
        if path.exists() and not args.force:
            print(f"{path} already exists (use --force to replace - old licenses stop working)")
            return 1
        key = Ed25519PrivateKey.generate()
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
        path.chmod(0o600)
        PUBLIC_KEY_FILE.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                                  serialization.PublicFormat.SubjectPublicKeyInfo))
        print(f"Private key: {path}  (KEEP SECRET, back it up)\nPublic key:  {PUBLIC_KEY_FILE}  (commit this)")
        return 0

    if args.cmd == "issue":
        key = serialization.load_pem_private_key(args.private.expanduser().read_bytes(), password=None)
        print(sign_license(key, args.licensee, args.max_people, args.expires))
        return 0

    try:
        lic = verify_license(args.key)
    except LicenseError as exc:
        print("INVALID:", exc)
        return 1
    print(f"valid: {lic.licensee}, max people: {lic.max_people or 'unlimited'}, expires: {lic.expires or 'never'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
