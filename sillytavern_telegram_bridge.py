#!/usr/bin/env python3
"""Telegram bridge executable entry point."""
from __future__ import annotations

from bridge.environment import bootstrap_environment


bootstrap_environment()
from bridge.main import main


if __name__ == "__main__":
    main()
