# Webhooks

Lighthouse delivers events as HTTP POST to subscriber URLs. Each
delivery carries an HMAC-SHA256 signature over the exact body bytes;
receivers verify before parsing.

## Event catalog

| Event | When | Payload `data` fields |
|---|---|---|
| `ping` | Operator hits `POST /v1/webhooks/{id}/test` | `{message}` |
| `importer.run.started` | A run row is claimed and about to drain | `importer_id`, `importer_name`, `importer_type`, `run_id`, `triggered_by` |
| `importer.run.finished` | Run completed (success OR error) | `importer_id`, `importer_name`, `importer_type`, `run_id`, `status` (`success` \| `error`), `chunks_added`, `error?` |

The envelope around `data` is identical on every event:

```json
{
  "event": "importer.run.finished",
  "ts": "2026-05-19T18:42:11.234567+00:00",
  "data": {
    "importer_id": "0123-…",
    "importer_name": "fastapi-docs",
    "importer_type": "sitemap",
    "run_id": "abcd-…",
    "status": "success",
    "chunks_added": 213
  }
}
```

## Subscribing

```bash
curl -X POST -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" \
  https://lighthouse.example.com/v1/webhooks/ \
  -d '{
    "url": "https://your-app.example.com/hooks/lighthouse",
    "events": ["importer.run.finished"]
  }'
```

Response (returned **once**; store the secret):

```json
{
  "id": "wh_…",
  "url": "https://your-app.example.com/hooks/lighthouse",
  "events": ["importer.run.finished"],
  "enabled": true,
  "secret": "rO3-FwUbN…",
  "created_at": "…"
}
```

`events: ["*"]` subscribes to everything. Rotate the secret with
`PATCH … {rotate_secret: true}` — the new secret is returned once,
old signatures fail immediately.

## Signing

Header on every POST:

```
X-Lighthouse-Signature: sha256=<hex digest>
X-Lighthouse-Event:     importer.run.finished
X-Lighthouse-Delivery:  <delivery uuid>
```

Verify with HMAC-SHA256 over the raw request body bytes:

```python
import hashlib, hmac
def verify(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header)
```

```ts
import { verifyWebhookSignature } from "@lighthouse/client";
const ok = await verifyWebhookSignature(
  secret,
  rawBodyBytes,
  request.headers["x-lighthouse-signature"]!,
);
```

Both SDKs ship this verifier. **Use the raw bytes** — parsing JSON
and re-serializing will change whitespace + key ordering and break
verification.

## Delivery semantics

- One POST per event per subscription.
- Receiver should respond `2xx` within **10s** to count as delivered.
- Non-2xx or network error → retry with exponential backoff:
  **30s → 2m → 10m → 1h**, then marked `dead`.
- Max attempts: **5** (configurable in future). After `dead` the row
  stops retrying; reset it with
  `POST /v1/webhooks/{id}/deliveries/{delivery_id}/redeliver`.
- The worker uses `SELECT … FOR UPDATE SKIP LOCKED` so multiple API
  pods don't double-deliver.

## Idempotency

`X-Lighthouse-Delivery` is unique per delivery (not per logical
event — retries reuse the id). Use it to dedupe replays. Within an
event stream, `run_id` + `event` together identify a logical
state-change exactly once per importer run.

## Local testing

Stand up a webhook receiver locally and target it from the engine:

```bash
# Receiver
python -m http.server 9000
# In another shell:
ngrok http 9000
# Register the ngrok URL via /v1/webhooks/, then trigger:
curl -X POST -H "Authorization: Bearer $TOK" \
  https://lighthouse.example.com/v1/webhooks/<id>/test
```

The `/test` endpoint enqueues a synthetic `ping` event — round-trips
URL reachability + signature verification without waiting for a real
importer run.
