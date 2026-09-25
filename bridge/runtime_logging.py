"""Runtime logging and private-path permissions for explicit application settings."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bridge.settings import AppSettings


def configure_logging(log_file: Path | None = None, *, app_settings: AppSettings) -> None:
    """Install the bridge rotating file handler and set root logging to INFO."""
    if log_file is None:
        log_file = app_settings.log_file
    target = Path(log_file).expanduser().resolve()
    root = logging.getLogger()

    if any(
        isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename).resolve() == target
        for handler in root.handlers
    ):
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(target),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    try:
        target.chmod(0o600)
    except OSError:
        logging.warning(
            "Could not protect runtime log file %s",
            target,
            exc_info=True,
        )


def enforce_runtime_permissions(*, app_settings: AppSettings) -> None:
    private_dirs = {
        app_settings.db_file.parent,
        app_settings.log_file.parent,
        app_settings.bridge_home / "backups",
        app_settings.character_backup_dir,
    }
    enforce_prompt_permissions = app_settings.enforce_prompt_permissions
    if app_settings.system_prompts_dir.exists() and (
        enforce_prompt_permissions or app_settings.system_prompts_dir.is_relative_to(app_settings.bridge_home.parent)
    ):
        private_dirs.add(app_settings.system_prompts_dir)
    for directory in private_dirs:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
        except OSError:
            logging.warning("Could not protect runtime directory %s", directory, exc_info=True)
    private_files = {
        app_settings.environment_file,
        app_settings.db_file,
        app_settings.log_file,
        app_settings.provider_config_file,
        app_settings.model_cache_file,
    }
    if app_settings.system_prompts_dir.exists() and (
        enforce_prompt_permissions or app_settings.system_prompts_dir.is_relative_to(app_settings.bridge_home.parent)
    ):
        private_files.update(app_settings.system_prompts_dir.glob("*.txt"))
        private_files.update(app_settings.system_prompts_dir.glob("*.json"))
    private_files.update(app_settings.db_file.parent.glob(app_settings.db_file.name + "-*"))
    for path in private_files:
        try:
            if path.is_file() and not path.is_symlink():
                path.chmod(0o600)
        except OSError:
            logging.warning("Could not protect runtime file %s", path, exc_info=True)
