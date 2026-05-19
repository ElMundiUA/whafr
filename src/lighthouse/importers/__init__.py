"""Admin-managed importers layer (engine-side feature).

A `LighthouseImporter` is a declarative wrapper around an existing
`Connector` — it adds a JSON-Schema config, a secret-key list, and a
factory that returns a ready-to-drain Connector. Engine operators add
importer *instances* through the admin UI; the runner picks them up,
decrypts secrets, builds the connector, and runs `ingest.drain()`
against it — same pipeline the legacy YAML recipes use.

This package is engine-side: nothing here ships any corpus content,
no role-recipe is registered as a builtin. Operators bring their own.
"""

from __future__ import annotations

# Side-effect import: registers every shipped adapter.
from lighthouse.importers import adapters as _adapters  # noqa: F401

# Importing the registry first ensures decorators on adapter modules
# attach to the populated table.
from lighthouse.importers.base import (  # noqa: F401
    ImporterMeta,
    ImporterRun,
    LighthouseImporter,
)
from lighthouse.importers.registry import (  # noqa: F401
    list_importers,
    lookup_importer,
    register,
)
