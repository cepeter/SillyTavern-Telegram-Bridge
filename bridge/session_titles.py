"""Pure session-title normalization shared by UI and persistence owners."""

from __future__ import annotations

SESSION_TITLE_MAX_CHARS = 80


def normalize_session_title(value: str) -> str:
    """Normalize and validate a user-visible session title."""
    title = " ".join(str(value or "").split())
    if not title or len(title) > SESSION_TITLE_MAX_CHARS or title.startswith("/"):
        raise ValueError(f"Session name must contain 1–{SESSION_TITLE_MAX_CHARS} characters and cannot start with /.")
    return title
