"""HMAC-SHA256 signature scheme for outgoing webhook deliveries.

Matches Stripe/GitHub's header convention:

    X-Lighthouse-Signature: sha256=<hex digest>

The digest covers the exact response body bytes — receivers should
verify before parsing JSON to keep the signature tied to the bytes
on the wire (not a re-serialized representation).
"""

from __future__ import annotations

import hashlib
import hmac


def sign_payload(secret: str, body: bytes) -> str:
    """Return the `sha256=<hex>` header value for `body` under `secret`."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, body: bytes, header_value: str) -> bool:
    """Constant-time verify. Returns True iff header matches."""
    if not header_value:
        return False
    expected = sign_payload(secret, body)
    return hmac.compare_digest(expected, header_value)
