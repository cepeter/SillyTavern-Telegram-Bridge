"""Permission changes are restricted to paths and booleans in the explicit settings value."""

from __future__ import annotations

import os
import stat

import pytest

from bridge.runtime_logging import enforce_runtime_permissions
from bridge.settings import load_app_settings


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode-bit contract")
def test_permission_setup_uses_explicit_environment_file_not_ambient(tmp_path, monkeypatch):
    selected = tmp_path / "selected.env"
    unrelated = tmp_path / "unrelated.env"
    for path in (selected, unrelated):
        path.write_text("# temporary fixture\n")
        path.chmod(0o644)
    settings = load_app_settings({"SILLYTAVERN_ENV_FILE": str(selected)}, home=tmp_path / "app")
    monkeypatch.setenv("SILLYTAVERN_ENV_FILE", str(unrelated))
    enforce_runtime_permissions(app_settings=settings)
    assert stat.S_IMODE(selected.stat().st_mode) == 0o600
    assert stat.S_IMODE(unrelated.stat().st_mode) == 0o644


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode-bit contract")
@pytest.mark.parametrize("value", ["true", "yes", "on", "1"])
def test_permission_setup_honors_validated_boolean(tmp_path, value):
    prompts = tmp_path / "external-prompts"
    prompts.mkdir()
    prompt = prompts / "fixture.txt"
    prompt.write_text("temporary fixture")
    prompt.chmod(0o644)
    settings = load_app_settings(
        {"SILLYTAVERN_SYSTEM_PROMPTS_DIR": str(prompts), "SILLYTAVERN_ENFORCE_PROMPT_PERMISSIONS": value},
        home=tmp_path / "app",
    )
    assert settings.enforce_prompt_permissions is True
    enforce_runtime_permissions(app_settings=settings)
    assert stat.S_IMODE(prompt.stat().st_mode) == 0o600
