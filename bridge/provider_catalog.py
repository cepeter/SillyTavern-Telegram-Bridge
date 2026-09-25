"""Provider catalog infrastructure adapter."""

from __future__ import annotations

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
