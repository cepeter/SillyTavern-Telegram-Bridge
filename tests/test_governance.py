"""Maintenance instructions and automation must match the executable repository."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_security_policy_has_real_disclosure_path_and_no_invented_sla():
    text = (ROOT / "SECURITY.md").read_text()
    assert "Security" in text and "private" in text.casefold()
    assert "main" in text and "preproduction" in text.casefold()
    assert "response-time" in text and "no guaranteed" in text.casefold()
    assert "security@" not in text
    assert "do not include" in text.casefold()


def test_contributor_commands_match_actual_tools():
    text = (ROOT / "CONTRIBUTING.md").read_text()
    for command in (
        "python -m ruff check .",
        "python -m ruff format --check .",
        "python tools/static_analysis.py",
        "python tools/check_dependency_lock.py",
        "python -m pytest",
        "--cov-report=json:coverage.json",
        "--require-hashes",
        "uv pip compile requirements.txt",
        "--generate-hashes",
        "requirements.lock",
        "git switch -c",
    ):
        assert command in text, command
    assert "custom requirements.lock" in text
    assert "do not assume" in text.casefold()
    assert "codeql" in text.casefold()


def test_dependabot_covers_pip_and_actions_without_unbounded_updates():
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    assert config["version"] == 2
    updates = {item["package-ecosystem"]: item for item in config["updates"]}
    assert set(updates) == {"pip", "github-actions"}
    for item in updates.values():
        assert item["directory"] == "/"
        assert item["schedule"]["interval"] == "weekly"
        assert 1 <= item["open-pull-requests-limit"] <= 3
        assert item.get("insecure-external-code-execution") != "allow"
    assert not list((ROOT / ".github/workflows").glob("*codeql*"))


def test_ci_checks_dependency_manifest_and_lock_compatibility():
    assert "python tools/check_dependency_lock.py" in (ROOT / ".github/workflows/ci.yml").read_text()
    assert "packaging==" in (ROOT / "requirements-dev.txt").read_text()


def test_public_examples_match_original_reviewed_hashes():
    baseline = json.loads((ROOT / "tools/public_examples.json").read_text())
    paths = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "config/system_prompts.example").glob("*")
        if path.is_file()
    }
    assert paths == set(baseline["sha256"])
    for name, expected in baseline["sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name


def test_repository_explicitly_ignores_python_tool_caches():
    ignored = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {".pytest_cache/", ".mypy_cache/", ".ruff_cache/"} <= ignored


USER_ENVIRONMENT_VARIABLES = {
    "SILLYTAVERN_TELEGRAM_BOT_TOKEN",
    "SILLYTAVERN_TELEGRAM_ALLOWED_USERS",
    "SILLYTAVERN_DIR",
    "SILLYTAVERN_DEFAULT_CHARACTER",
    "SILLYTAVERN_MODEL",
    "SILLYTAVERN_DEFAULT_USER_NAME",
    "LLM_API_KEY",
    "SILLYTAVERN_ENV_FILE",
    "SILLYTAVERN_BRIDGE_HOME",
    "SILLYTAVERN_BRIDGE_SOURCE_DIR",
    "SILLYTAVERN_LIVE_BRIDGE_DIR",
    "SILLYTAVERN_PROVIDER_CONFIG",
    "SILLYTAVERN_MODEL_CACHE",
    "SILLYTAVERN_MODEL_REFRESH_SECONDS",
    "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS",
    "SILLYTAVERN_PROVIDER_PRIVATE_HOSTS",
    "SILLYTAVERN_CONTEXT_WINDOW_TOKENS",
    "SILLYTAVERN_CONTEXT_OUTPUT_RESERVE_TOKENS",
    "SILLYTAVERN_CONTEXT_HISTORY_CANDIDATES",
    "SILLYTAVERN_CHARACTER_DIR",
    "SILLYTAVERN_CHARACTER_BACKUP_DIR",
    "SILLYTAVERN_WORLD_DIR",
    "SILLYTAVERN_SYSTEM_PROMPTS_DIR",
    "SILLYTAVERN_NATIVE_SETTINGS_FILE",
    "SILLYTAVERN_NATIVE_AVATAR_DIR",
    "SILLYTAVERN_ENFORCE_PROMPT_PERMISSIONS",
    "SILLYTAVERN_PERF_LOG",
    "HINDSIGHT_API_URL",
    "HINDSIGHT_API_KEY",
    "SILLYTAVERN_HINDSIGHT_ALLOWED_HOSTS",
    "SILLYTAVERN_HINDSIGHT_PRIVATE_HOSTS",
    "SILLYTAVERN_RAG_EMBEDDING_URL",
    "SILLYTAVERN_RAG_EMBEDDING_API_KEY",
    "SILLYTAVERN_RAG_ALLOWED_HOSTS",
    "SILLYTAVERN_RAG_PRIVATE_HOSTS",
    "SILLYTAVERN_RAG_EMBEDDING_MODEL",
    "SILLYTAVERN_RAG_EMBEDDING_DIMENSIONS",
    "SILLYTAVERN_RAG_EMBEDDING_REVISION",
    "SILLYTAVERN_RAG_MAX_EXTRACTED_CHARS",
    "SILLYTAVERN_RAG_MAX_PDF_PAGES",
    "SILLYTAVERN_RAG_PDF_PARSE_TIMEOUT_SECONDS",
    "SILLYTAVERN_RAG_SEMANTIC_CANDIDATES",
    "SILLYTAVERN_SYNC_API_URL",
    "SILLYTAVERN_SYNC_API_HANDLE",
    "SILLYTAVERN_SYNC_API_PASSWORD",
    "SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS",
    "SILLYTAVERN_SYNC_API_INTERVAL_SECONDS",
    "SILLYTAVERN_STT_MODEL",
    "SILLYTAVERN_TTS_BIN",
    "SILLYTAVERN_TTS_VOICE",
    "OPENCODE_CLIENT_VERSION",
    "SILLYTAVERN_UPDATE_ALLOWED_SIGNERS",
    "SILLYTAVERN_UPDATE_SERVICE",
}


def test_user_readme_and_env_example_cover_supported_environment_variables():
    readme = (ROOT / "README.md").read_text()
    example = (ROOT / ".env.example").read_text()
    missing_readme = sorted(name for name in USER_ENVIRONMENT_VARIABLES if name not in readme)
    missing_example = sorted(name for name in USER_ENVIRONMENT_VARIABLES if name not in example)
    assert missing_readme == []
    assert missing_example == []


def test_user_readme_documents_provider_catalog_controls():
    readme = (ROOT / "README.md").read_text()
    for field in (
        "name",
        "api_endpoint",
        "api_key_env",
        "transport",
        "adapter",
        "streaming",
        "models",
        "discover_models",
        "health_check",
        "extra_headers",
        "anthropic_version",
        "image_enabled",
        "image_endpoint",
        "image_models",
    ):
        assert f"`{field}`" in readme, field
    provider_example = (ROOT / "config/providers.example.yaml").read_text()
    assert "image_default_size" not in provider_example
