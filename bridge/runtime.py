"""Compatibility facade for the staged bridge runtime.

Domain files still share one namespace so existing handlers keep their call-time
late binding semantics. The loader now validates load phases and makes
intentional recovery/safety overrides explicit and auditable.
"""
from __future__ import annotations

from pathlib import Path as _RuntimePath

from bridge.runtime_loader import (
    DEFAULT_RUNTIME_STAGES as _DEFAULT_RUNTIME_STAGES,
    load_runtime_namespace as _load_runtime_namespace,
)


RUNTIME_LOAD_REPORT = _load_runtime_namespace(
    globals(),
    _RuntimePath(__file__).parent,
    _DEFAULT_RUNTIME_STAGES,
)

del _RuntimePath, _DEFAULT_RUNTIME_STAGES, _load_runtime_namespace
