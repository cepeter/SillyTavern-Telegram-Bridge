#!/usr/bin/env python3
"""Telegram bridge executable entry point."""

from __future__ import annotations

from bridge.config_values import ConfigurationError
from bridge.environment import bootstrap_environment

try:
    bootstrap_environment()
    from bridge.main import main
except ConfigurationError as exc:
    raise SystemExit(str(exc)) from None

if __name__ == "__main__":
    main()
