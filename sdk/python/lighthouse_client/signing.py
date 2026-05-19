"""Verify Lighthouse webhook signatures on the receiver side."""

from __future__ import annotations

import hashlib
import hmac


def verify_webhook_signature(secret: str, body: bytes, header_value: str) -> bool:
    """Returns True iff `header_value` matches HMAC-SHA256(secret, body).

    Pass the raw request body bytes (NOT a re-serialized JSON dict)
    so the signature stays valid byte-for-byte."""
    if not header_value or not header_value.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header_value)
