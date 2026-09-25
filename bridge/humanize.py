"""Optional per-session prose humanization pass.

Humanization rewrites the model's visible response so it reads like a person
wrote it instead of a chatbot. It is opt-in per session and defaults to off.

The rewrite instructions are a compact bridge-specific adaptation of the MIT
licensed ``blader/humanizer`` agent skill (https://github.com/blader/humanizer),
which is itself derived from Wikipedia's "Signs of AI writing". Only the prompt
behavior is reproduced here; no upstream code or runtime dependency is vendored.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from bridge.config import GENERATION_DEFAULTS
from bridge.provider_port import ProviderPort

HUMANIZER_OFF = "off"
HUMANIZER_ON = "on"

_HUMANIZER_VALUES = {HUMANIZER_OFF, HUMANIZER_ON}
_HUMANIZER_ALIASES = {
    "true": HUMANIZER_ON,
    "yes": HUMANIZER_ON,
    "enabled": HUMANIZER_ON,
    "1": HUMANIZER_ON,
    "false": HUMANIZER_OFF,
    "no": HUMANIZER_OFF,
    "disabled": HUMANIZER_OFF,
    "0": HUMANIZER_OFF,
}


def normalize_humanizer(value: str | None) -> str:
    """Return ``"off"`` or ``"on"`` or raise ValueError for unknown input."""
    normalized = str(value or "").strip().casefold()
    normalized = _HUMANIZER_ALIASES.get(normalized, normalized)
    if normalized not in _HUMANIZER_VALUES:
        raise ValueError("use on or off")
    return normalized


def humanizer_enabled(value: str | None) -> bool:
    """True when the session has the humanizer response style enabled."""
    return normalize_humanizer(value or HUMANIZER_OFF) == HUMANIZER_ON


def humanizer_label(value: str | None) -> str:
    return "On" if humanizer_enabled(value) else "Off"


HUMANIZER_SYSTEM_PROMPT = (
    "You are a prose humanizer. Rewrite the supplied text so it reads like a "
    "person wrote it, not a chatbot. Keep every fact, name, number, date, "
    "quote, citation, and claim. Do not add anything that is not already "
    "present.\n\n"
    "Remove these AI-writing patterns:\n"
    '- "not X but Y" contrasts and other staged contrasts\n'
    "- one-line closers and dramatic fragments that restate the point\n"
    '- staged run-ups ("Let\'s dive in", "Here\'s the thing", "Honestly?")\n'
    '- arguing with no one ("To be clear", "This isn\'t about X")\n'
    "- forced triads and repeated sentence openings\n"
    "- em dashes and en dashes used as connectors (use commas, periods, or rewrite)\n"
    '- stacked qualifiers ("could potentially", "might arguably")\n'
    "- overused AI words (delve, crucial, pivotal, showcase, testament, "
    "landscape, tapestry, robust, vibrant, fostering, underscore, highlight)\n"
    '- inflated significance and sales language ("stands as a testament", '
    '"nestled", "breathtaking", "in the heart of")\n'
    '- borrowed authority ("experts say", "cited in ...")\n'
    "- bold used as decoration and decorative headings\n"
    '- chatbot residue ("Great question!", "I hope this helps", '
    '"Let me know if...")\n'
    "- knowledge-limit disclaimers and guesses presented as fact\n\n"
    "Preserve exactly: code blocks and inline code, commands, file paths, "
    "URLs, links, Markdown structure required by the target format, character "
    "dialogue, and roleplay action markers. Do not flatten a character's voice "
    "or rewrite dialogue. Keep specific, unusual details.\n\n"
    "Return only the rewritten text. Do not explain, summarize, preface, or "
    "add a heading."
)


def render_humanized_response(
    api_key: str,
    model: str,
    text: str,
    session_id: str,
    settings: dict[str, object] | None = None,
    *,
    provider_port: ProviderPort,
) -> str:
    """Run one bounded humanization pass; fail open to the original text.

    Any provider failure, timeout, or import error returns the original text so
    a humanizer pass can never fail the user's turn. Callers are responsible for
    only invoking this pass when the session has humanization enabled.
    """
    if not text.strip():
        return text
    render_settings = dict(settings or GENERATION_DEFAULTS)
    render_settings.update({"temperature": 0.2, "reasoning_budget": 0, "stop_sequences": ""})
    messages = [
        {"role": "system", "content": HUMANIZER_SYSTEM_PROMPT},
        {"role": "user", "content": "<source_text>\n" + text + "\n</source_text>"},
    ]
    try:
        rewritten = provider_port.generate(
            api_key,
            model,
            messages,
            session_id=f"{session_id}:humanize",
            settings=render_settings,
        )
    except Exception:
        logging.warning("Humanizer pass failed; keeping the original response", exc_info=True)
        return text
    cleaned = str(rewritten or "").strip()
    return cleaned if cleaned else text


def set_humanizer(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    value: str,
    operation_id: int | str | None = None,
    *,
    update_session: Callable[..., object],
) -> str:
    """Persist a validated humanizer state for one session."""
    normalized = normalize_humanizer(value)
    update_session(
        db,
        chat_id,
        session_id,
        operation_id=operation_id,
        operation_kind="humanizer_select",
        humanizer=normalized,
    )
    return normalized
