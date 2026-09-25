"""Configuration errors identify the setting without exposing its raw value."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("value", ["0", "-1", "bad-sensitive-value", "1.5", "10001"])
def test_numeric_configuration_error_is_named(value):
    values = importlib.import_module("bridge.config_values")
    name = "SILLYTAVERN_RAG_MAX_PDF_PAGES"
    with pytest.raises(values.ConfigurationError, match=name) as error:
        values.read_int({name: value}, name, 200, minimum=1, maximum=10000)
    assert "bad-sensitive-value" not in str(error.value)


def test_integer_defaults_and_boundaries():
    values = importlib.import_module("bridge.config_values")
    assert values.read_int({}, "LIMIT", 4, minimum=1, maximum=10) == 4
    assert values.read_int({"LIMIT": " 10 "}, "LIMIT", 4, minimum=1, maximum=10) == 10
    assert values.read_int({"LIMIT": "1"}, "LIMIT", 4, minimum=1, maximum=10) == 1


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "1e309", "-1", "11"])
def test_float_configuration_is_finite_and_bounded(value):
    values = importlib.import_module("bridge.config_values")
    with pytest.raises(values.ConfigurationError, match="LIMIT"):
        values.read_float({"LIMIT": value}, "LIMIT", 1.0, minimum=0.1, maximum=10)


def test_imported_config_uses_named_numeric_error():
    env = dict(os.environ, SILLYTAVERN_RAG_MAX_PDF_PAGES="not-a-number")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; from pathlib import Path; from bridge.settings import load_app_settings; "
                "load_app_settings(os.environ, home=Path.home())"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "SILLYTAVERN_RAG_MAX_PDF_PAGES" in result.stderr
    assert "ConfigurationError:" in result.stderr
    assert "not-a-number" not in result.stderr


def test_launcher_reports_configuration_error_without_traceback(tmp_path):
    env = dict(
        os.environ,
        SILLYTAVERN_ENV_FILE=str(tmp_path / "absent.env"),
        SILLYTAVERN_RAG_MAX_PDF_PAGES="invalid-private-value",
    )
    result = subprocess.run(
        [sys.executable, "sillytavern_telegram_bridge.py", "--check"], env=env, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "SILLYTAVERN_RAG_MAX_PDF_PAGES" in result.stderr
    assert "Traceback" not in result.stderr
    assert "invalid-private-value" not in result.stderr
