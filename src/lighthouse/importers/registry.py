"""Type registry. Lookup by `type` string, list all types for the UI.

Adapters call `@register` at import time. The package `__init__` does
a side-effect import of `adapters/` so registrations happen before
the API router or runner reads the table.
"""

from __future__ import annotations

from collections.abc import Iterable

from lighthouse.importers.base import LighthouseImporter

_REGISTRY: dict[str, type[LighthouseImporter]] = {}


def register(cls: type[LighthouseImporter]) -> type[LighthouseImporter]:
    """Class decorator: add a LighthouseImporter subclass to the registry.

    The class must set `meta` at class-level with a unique `type` key.
    Raises if the key collides — silent shadowing is worse than the
    crash at boot.
    """
    key = cls.meta.type
    existing = _REGISTRY.get(key)
    if existing is not None and existing is not cls:
        raise RuntimeError(
            f"Importer type {key!r} is already registered "
            f"by {existing.__module__}.{existing.__qualname__}"
        )
    _REGISTRY[key] = cls
    return cls


def lookup_importer(type_key: str) -> type[LighthouseImporter]:
    """Resolve `type` string to the class. Raises KeyError if unknown."""
    try:
        return _REGISTRY[type_key]
    except KeyError as exc:
        raise KeyError(
            f"Unknown importer type {type_key!r}. "
            f"Registered: {sorted(_REGISTRY)}"
        ) from exc


def list_importers() -> Iterable[type[LighthouseImporter]]:
    """All registered classes, in registration order."""
    return _REGISTRY.values()
