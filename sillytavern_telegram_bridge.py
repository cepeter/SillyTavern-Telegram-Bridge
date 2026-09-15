#!/usr/bin/env python3
"""Telegram bridge entrypoint.

Implementation is split across the modules in ``bridge/``.
"""
from __future__ import annotations

import os
from pathlib import Path


def bootstrap_env() -> None:
    env_path = Path(os.environ.get(
        "SILLYTAVERN_ENV_FILE",
        str(Path.home() / ".local/share/sillytavern-telegram/.env"),
    ))
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


bootstrap_env()
from bridge.runtime import main

if __name__ == "__main__":
    main()
