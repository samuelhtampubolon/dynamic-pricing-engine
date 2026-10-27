"""Client-secret hashing (scrypt) with no dependency on application settings.

Kept separate from ``src.security`` so hashes can be generated in a shell where
the production settings are not (yet) complete:

    python -m src.credentials hash-secret
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Optional

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _SCRYPT_DKLEN = 2**14, 8, 1, 32
MIN_SECRET_LENGTH = 12


def hash_client_secret(secret: str, salt: Optional[bytes] = None) -> str:
    """Return ``scrypt$<salt_b64>$<hash_b64>`` for a client secret."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        secret.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN
    )
    return "scrypt${}${}".format(
        base64.urlsafe_b64encode(salt).decode(), base64.urlsafe_b64encode(digest).decode()
    )


def is_valid_hash(encoded: str) -> bool:
    parts = encoded.split("$")
    if len(parts) != 3 or parts[0] != "scrypt":
        return False
    try:
        return len(base64.urlsafe_b64decode(parts[1])) >= 16 and len(base64.urlsafe_b64decode(parts[2])) >= 32
    except (ValueError, TypeError):
        return False


def verify_client_secret(secret: str, encoded: str) -> bool:
    """Constant-time comparison of ``secret`` against an encoded scrypt hash."""
    if not is_valid_hash(encoded):
        return False
    _, salt_b64, hash_b64 = encoded.split("$")
    salt = base64.urlsafe_b64decode(salt_b64)
    expected = base64.urlsafe_b64decode(hash_b64)
    candidate = hashlib.scrypt(
        secret.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=len(expected)
    )
    return hmac.compare_digest(candidate, expected)


if __name__ == "__main__":  # pragma: no cover - CLI helper
    import getpass
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "hash-secret":
        value = getpass.getpass(f"Client secret (min {MIN_SECRET_LENGTH} chars): ")
        if len(value) < MIN_SECRET_LENGTH:
            sys.exit("Secret too short.")
        print(hash_client_secret(value))
    else:
        print("Usage: python -m src.credentials hash-secret")
