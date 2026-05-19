"""Secret encryption for admin-managed importer configs.

Uses Fernet (AES-128-CBC + HMAC-SHA256 over a 32-byte key) from
`cryptography`. The master key lives in the env var
`LIGHTHOUSE_SECRETS_KEY` — same value across every replica that
reads the same DB. Generate a fresh one with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

If the env var is missing we still allow read/write of importers
that declare no secret keys, but any attempt to persist or decrypt
a secret-bearing config raises `MissingMasterKeyError` so a misconfigured
engine fails loudly instead of silently writing plain-text PATs.
"""

from __future__ import annotations

import json
import os
from functools import cache

from cryptography.fernet import Fernet, InvalidToken


class MissingMasterKeyError(RuntimeError):
    """LIGHTHOUSE_SECRETS_KEY isn't set or isn't a valid Fernet key."""


class SecretsCorruptError(RuntimeError):
    """The DB blob can't be decrypted with the current master key."""


@cache
def _fernet() -> Fernet:
    raw = os.environ.get("LIGHTHOUSE_SECRETS_KEY")
    if not raw:
        raise MissingMasterKeyError(
            "LIGHTHOUSE_SECRETS_KEY is not set. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and set it on "
            "every Lighthouse replica."
        )
    try:
        return Fernet(raw.encode() if isinstance(raw, str) else raw)
    except (ValueError, TypeError) as exc:
        raise MissingMasterKeyError(
            "LIGHTHOUSE_SECRETS_KEY is not a valid Fernet key "
            "(must be 32 url-safe-base64 bytes)."
        ) from exc


def has_master_key() -> bool:
    """Whether secrets can be encrypted/decrypted in this process."""
    return bool(os.environ.get("LIGHTHOUSE_SECRETS_KEY"))


def encrypt_secrets(secrets: dict[str, str]) -> bytes:
    """Encrypt a `{key: value}` map → opaque blob suitable for BYTEA."""
    return _fernet().encrypt(json.dumps(secrets, sort_keys=True).encode())


def decrypt_secrets(blob: bytes | memoryview | None) -> dict[str, str]:
    """Inverse of `encrypt_secrets`. Empty blob → empty dict."""
    if blob is None:
        return {}
    data = bytes(blob) if isinstance(blob, memoryview) else blob
    if not data:
        return {}
    try:
        return json.loads(_fernet().decrypt(data))
    except InvalidToken as exc:
        raise SecretsCorruptError(
            "Importer secrets blob can't be decrypted — "
            "LIGHTHOUSE_SECRETS_KEY likely rotated."
        ) from exc
