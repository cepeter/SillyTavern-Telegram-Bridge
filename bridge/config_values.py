"""Pure bounded setting readers with non-secret, actionable diagnostics."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping


class ConfigurationError(RuntimeError):
    """An operator setting is invalid; raw values are intentionally omitted."""


def read_int(environ: Mapping[str, str], name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = environ.get(name, str(default)).strip()
    try:
        if not re.fullmatch(r"[+-]?[0-9]+", raw):
            raise ValueError("not a decimal integer")
        value = int(raw)
    except ValueError:
        raise ConfigurationError(f"{name} must be an integer between {minimum} and {maximum}") from None
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def read_float(environ: Mapping[str, str], name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(environ.get(name, str(default)))
    except (ValueError, OverflowError):
        raise ConfigurationError(f"{name} must be a finite number between {minimum} and {maximum}") from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be a finite number between {minimum} and {maximum}")
    return value
