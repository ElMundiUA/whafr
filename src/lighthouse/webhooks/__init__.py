"""Outbound webhooks.

Lighthouse fires events (importer.run.started, importer.run.finished,
…) into `webhook_deliveries`; a worker drains the queue and POSTs to
each subscribed URL with an HMAC-SHA256 signature header. Retries
with exponential backoff; gives up after N attempts.

Consumers verify the signature like::

    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, request.headers["X-Lighthouse-Signature"]):
        return 401
"""

from __future__ import annotations

from lighthouse.webhooks.dispatcher import emit_event, run_worker  # noqa: F401
from lighthouse.webhooks.signing import sign_payload, verify_signature  # noqa: F401
