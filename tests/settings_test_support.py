"""Explicit per-test configuration builders; no application modules are patched.

Builders are mutable test inputs. Every product call receives a real frozen
AppSettings snapshot, so tests can change inputs without a global config facade.
"""

from __future__ import annotations

import dataclasses
import os
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bridge.settings import AppSettings, load_app_settings


def make_test_settings(
    environ: Mapping[str, str] | None = None,
    *,
    base: AppSettings | None = None,
    home: Path | None = None,
    **overrides: Any,
) -> AppSettings:
    settings = base or load_app_settings(dict(os.environ) if environ is None else environ, home=home or Path.home())
    if ("character_dir" in overrides or "default_character_file" in overrides) and "card_file" not in overrides:
        overrides["card_file"] = Path(overrides.get("character_dir", settings.character_dir)) / overrides.get(
            "default_character_file", settings.default_character_file
        )
    return dataclasses.replace(settings, **overrides) if overrides else settings


class SettingsBuilder:
    """Per-test inputs supporting patch.object without mutating frozen settings."""

    def __init__(self) -> None:
        object.__setattr__(self, "_overrides", {})

    def build(self) -> AppSettings:
        return make_test_settings(**self._overrides)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.build(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name not in AppSettings.__dataclass_fields__:
            raise AttributeError(name)
        self._overrides[name] = value

    def __delattr__(self, name: str) -> None:
        if name in self._overrides:
            del self._overrides[name]
        else:
            raise AttributeError(name)


class SettingsTestCase(unittest.TestCase):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.app_settings_builder = SettingsBuilder()
