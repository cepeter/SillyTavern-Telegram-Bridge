"""Canonical generation settings values owner."""

from __future__ import annotations

from bridge import config as _config


def format_generation_settings(settings: dict[str, object]) -> str:
    stop = str(settings.get("stop_sequences") or "") or "off"
    reasoning_budget = int(settings.get("reasoning_budget") or 0)
    reasoning_level = next(
        (name for name, value in _config.REASONING_LEVELS.items() if value == reasoning_budget), "custom"
    )
    return (
        f"temperature={settings['temperature']}\nmax_tokens={settings['max_tokens']}\n"
        f"top_p={settings['top_p']}\nfrequency_penalty={settings['frequency_penalty']}\n"
        f"presence_penalty={settings['presence_penalty']}\nreasoning={reasoning_level} ({reasoning_budget})\n"
        f"stop={stop}"
    )


def parse_generation_setting(key: str, raw_value: str) -> tuple[str, object]:
    aliases = {
        "temp": "temperature",
        "max": "max_tokens",
        "top-p": "top_p",
        "frequency": "frequency_penalty",
        "presence": "presence_penalty",
        "reasoning": "reasoning_budget",
        "stop": "stop_sequences",
    }
    key = aliases.get(key.casefold(), key.casefold())
    if key not in _config.GENERATION_DEFAULTS:
        raise ValueError("unknown setting")
    if key == "stop_sequences":
        if raw_value.casefold() in {"off", "none", "clear"}:
            return key, ""
        values = [item.strip() for item in raw_value.replace("\\n", "\n").split(",") if item.strip()]
        if len(values) > 4 or any(len(item) > 100 for item in values):
            raise ValueError("stop supports up to 4 sequences of 100 characters")
        return key, "\n".join(values)
    if key == "reasoning_budget" and raw_value.casefold() in _config.REASONING_LEVELS:
        return key, _config.REASONING_LEVELS[raw_value.casefold()]
    try:
        if key in {"max_tokens", "reasoning_budget"}:
            value = int(raw_value)
            limits = {"max_tokens": (1, 16000), "reasoning_budget": (0, 32000)}
        else:
            value = float(raw_value)
            limits = {
                "temperature": (0.0, 2.0),
                "top_p": (0.0, 1.0),
                "frequency_penalty": (-2.0, 2.0),
                "presence_penalty": (-2.0, 2.0),
            }
        low, high = limits[key]
        if not low <= value <= high:
            raise ValueError(f"value must be between {low} and {high}")
        return key, value
    except ValueError as exc:
        if "between" in str(exc) or "supports" in str(exc):
            raise
        raise ValueError("value has the wrong format") from exc
