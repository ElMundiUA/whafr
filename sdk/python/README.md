# lighthouse-client

Async-first Python client for the Lighthouse engine API.

## Install

```bash
pip install lighthouse-client
```

## Use

```python
from lighthouse_client import AsyncLighthouse, verify_webhook_signature

async with AsyncLighthouse("https://lighthouse.example.com", token="…") as lh:
    stats = await lh.corpus_stats()
    print(f"{stats.total_chunks} chunks across {stats.total_sources} sources")

    hits = await lh.search("OAuth 2.0 PKCE S256", top_k=5)
    for h in hits.hits:
        print(h.summary)

    imp = await lh.create_importer(
        type="sitemap",
        name="fastapi-docs",
        recipe="fastapi-docs",
        config={"root": "https://fastapi.tiangolo.com", "max_pages": 0},
    )
    await lh.run_importer(imp.id)

    wh = await lh.create_webhook(
        url="https://your-app.example.com/webhooks/lighthouse",
        events=["importer.run.finished"],
    )
    # Persist wh.secret; receivers verify with:
    ok = verify_webhook_signature(wh.secret, raw_body_bytes, request.headers["X-Lighthouse-Signature"])
```

A small sync client (`Lighthouse`) is provided for one-shot scripts;
prefer `AsyncLighthouse` everywhere else.
