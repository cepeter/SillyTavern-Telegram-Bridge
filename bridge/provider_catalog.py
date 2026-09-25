"""Provider catalog infrastructure adapter."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

import yaml

from bridge.config import PROVIDER_CONFIG_FILE


def load_provider_catalog(path: Path = PROVIDER_CONFIG_FILE) -> Mapping[str, object]:
    try:
        config = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:
        logging.warning("Could not read provider catalog", exc_info=True)
        return {}
    providers = config.get("providers") if isinstance(config, dict) else None
    return providers if isinstance(providers, dict) else {}
