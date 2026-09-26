"""Bounded Light Novel protocol, separate from visible story prose."""

from __future__ import annotations

import json
import re
import unicodedata

from bridge.telegram_output import telegram_safe_output

MAX_CHOICE_CHARS = 160
MAX_RESPONSE_CHARS = 96000


def validate_choices(value: object, requested_count: int) -> list[str]:
    if requested_count not in {2, 3, 4} or not isinstance(value, list) or len(value) != requested_count:
        raise ValueError("Expected exactly the requested 2–4 choices")
    choices = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Choices must be text")
        text = " ".join(telegram_safe_output(item).split())
        if not text or len(text) > MAX_CHOICE_CHARS or text.startswith(("/", "@")):
            raise ValueError("Choices must be short narrative actions, not commands")
        if any(unicodedata.category(char).startswith("C") for char in text):
            raise ValueError("Choice contains control characters")
        choices.append(text)
    if len({unicodedata.normalize("NFKC", text).casefold() for text in choices}) != len(choices):
        raise ValueError("Choices must be distinct")
    return choices


def _unfence(source: str) -> str:
    if len(source) > MAX_RESPONSE_CHARS:
        raise ValueError("Light Novel output exceeds the bounded protocol size")
    text = source.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    return fenced.group(1).strip() if fenced else text


def parse_story_response(source: str, requested_count: int) -> tuple[str, list[str] | None]:
    text = _unfence(source)
    try:
        result = json.loads(text)
    except (ValueError, TypeError):
        result = None
    if isinstance(result, dict):
        story = result.get("story")
        if not isinstance(story, str) or not story.strip():
            raise ValueError("Story response has no usable narrative")
        try:
            choices = validate_choices(result.get("choices"), requested_count)
        except ValueError:
            choices = None
        return story.strip(), choices
    # A malformed choice tail must not discard an already complete JSON story string.
    if text.startswith("{"):
        match = re.match(r'\{\s*"story"\s*:\s*', text)
        if match:
            try:
                story, _end = json.JSONDecoder().raw_decode(text[match.end() :])
                if isinstance(story, str) and story.strip():
                    return story.strip(), None
            except ValueError:
                pass
        raise ValueError("Malformed story envelope; no complete narrative to recover")
    if not text or text.startswith(("[", "```")):
        raise ValueError("Story response has no usable narrative")
    # Some providers ignore the envelope instruction entirely; preserve ordinary prose only.
    return text, None


def parse_choice_response(source: str, requested_count: int) -> list[str]:
    value = json.loads(_unfence(source))
    return validate_choices(value.get("choices") if isinstance(value, dict) else value, requested_count)


def inline_instruction(count: int, language: str) -> str:
    return (
        "Light Novel response contract: return a JSON object with exactly two keys: "
        '"story" (the complete narrative as a JSON string) and "choices" (an array of '
        f"exactly {count} distinct next actions for the USER, each 1–{MAX_CHOICE_CHARS} characters). "
        "Do not choose for the user, predict outcomes, put menu text inside the story, or include slash commands. "
        "Preserve the character, persona, world and all established story context. "
        f"Both narrative and actions must match response language {language or 'auto (the conversation language)'}. "
        "No markdown fences, explanations or extra keys."
    )


def add_inline_contract(messages: list[dict], count: int, language: str) -> list[dict]:
    result = [dict(message) for message in messages]
    instruction = inline_instruction(count, language)
    if result and result[0].get("role") == "system":
        result[0]["content"] = str(result[0].get("content") or "") + "\n\n" + instruction
    else:
        result.insert(0, {"role": "system", "content": instruction})
    return result
