"""Provider catalog infrastructure adapter."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path

import yaml

from bridge.settings import AppSettings


def load_provider_catalog(path: Path | None = None, *, app_settings: AppSettings) -> Mapping[str, object]:
    if path is None:
        path = app_settings.provider_config_file
    try:
        config = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:
        logging.warning("Could not read provider catalog", exc_info=True)
        return {}
    providers = config.get("providers") if isinstance(config, dict) else None
    return providers if isinstance(providers, dict) else {}


def load_routing_catalog(*, app_settings: AppSettings) -> Mapping[str, object]:
    """Combine configured models with opted-in discovery results without network I/O.

    The cache supplies model identifiers only, never endpoints or credentials.
    Explicit YAML models remain valid when discovery is unavailable or stale.
    """
    providers = load_provider_catalog(app_settings=app_settings)
    try:
        raw = json.loads(app_settings.model_cache_file.read_text(encoding="utf-8"))
        cache = raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        cache = {}
    result: dict[str, object] = {}
    for provider_id, raw_spec in providers.items():
        if not isinstance(raw_spec, Mapping):
            continue
        spec = dict(raw_spec)
        configured = spec.get("models")
        models = list(configured) if isinstance(configured, (list, tuple)) else []
        cached = cache.get(provider_id)
        if spec.get("discover_models") is True and isinstance(cached, dict):
            discovered = cached.get("models")
            if isinstance(discovered, list):
                models.extend(discovered)
        spec["models"] = list(dict.fromkeys(item for item in models if isinstance(item, str) and item))
        result[str(provider_id)] = spec
    return result
