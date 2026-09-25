"""Character-card, World Info, and System Prompt content helpers."""

import base64
import hashlib
import json
import logging
import random
import re
import struct
import time
from pathlib import Path

from bridge import config as _config
from bridge.native_cache import (
    cached_json,
    cached_png_metadata,
    cached_text,
)
from bridge.panel_utils import panel_label


def read_png_chara(path: Path) -> dict:
    return cached_png_metadata(path, lambda current: parse_png_chara_bytes(current.read_bytes()))


def parse_png_chara_bytes(raw: bytes) -> dict:
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("character file is not a PNG")
    pos = 8
    encoded = None
    while pos + 12 <= len(raw):
        size = struct.unpack(">I", raw[pos : pos + 4])[0]
        chunk_type = raw[pos + 4 : pos + 8]
        chunk = raw[pos + 8 : pos + 8 + size]
        pos += 12 + size
        if chunk_type == b"tEXt" and chunk.startswith(b"chara\x00"):
            encoded = chunk.split(b"\x00", 1)[1]
            break
    if not encoded:
        raise ValueError("PNG has no SillyTavern chara metadata")
    return json.loads(base64.b64decode(encoded).decode("utf-8"))


def _default_character_name() -> str:
    """Fallback display name, derived from the configured default card file."""
    return Path(_config.DEFAULT_CHARACTER_FILE).stem.strip() or "Character"


def card_fields(card: dict) -> dict[str, str]:
    data = card.get("data") if isinstance(card.get("data"), dict) else card
    fields = {}
    for key in (
        "name",
        "description",
        "personality",
        "scenario",
        "first_mes",
        "mes_example",
        "system_prompt",
        "post_history_instructions",
    ):
        value = str(data.get(key) or card.get(key) or "")
        fields[key] = value[:120] if key == "name" else value[: _config.CARD_FIELD_MAX_CHARS]
    total = sum(len(value) for key, value in fields.items() if key != "name")
    if total > _config.CARD_TOTAL_MAX_CHARS:
        remaining = _config.CARD_TOTAL_MAX_CHARS
        for key in (
            "description",
            "personality",
            "scenario",
            "first_mes",
            "mes_example",
            "system_prompt",
            "post_history_instructions",
        ):
            fields[key] = fields[key][:remaining]
            remaining = max(0, remaining - len(fields[key]))
    fields["name"] = fields["name"] or _default_character_name()
    alternate = data.get("alternate_greetings") or card.get("alternate_greetings") or []
    if not isinstance(alternate, list):
        alternate = []
    fields["alternate_greetings"] = json.dumps(
        [str(value)[: _config.CARD_FIELD_MAX_CHARS] for value in alternate[:20]], ensure_ascii=False
    )
    return fields


def character_card_paths() -> list[Path]:
    if not _config.CHARACTER_DIR.exists():
        return []
    return sorted(p for p in _config.CHARACTER_DIR.glob("*.png") if p.is_file())[: _config.CATALOG_MAX_ITEMS]


def safe_character_path(name: str) -> Path | None:
    base = _config.CHARACTER_DIR.resolve()
    path = (_config.CHARACTER_DIR / name).resolve()
    if path.parent != base or path.suffix.lower() != ".png" or not path.is_file():
        return None
    return path


def card_fields_from_file(name: str) -> dict[str, str]:
    path = safe_character_path(name) or _config.CARD_FILE
    return card_fields(read_png_chara(path))


def world_file_paths() -> list[Path]:
    if not _config.WORLD_DIR.exists():
        return []
    return sorted(p for p in _config.WORLD_DIR.glob("*.json") if p.is_file())[: _config.CATALOG_MAX_ITEMS]


def safe_world_path(name: str) -> Path | None:
    if not name or name == "off":
        return None
    base = _config.WORLD_DIR.resolve()
    path = (_config.WORLD_DIR / name).resolve()
    if path.parent != base or path.suffix.lower() != ".json" or not path.is_file():
        return None
    return path


def active_world_files(value: str | list[str] | None) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        text = str(value or "").strip()
        if not text:
            return []
        try:
            parsed = json.loads(text) if text.startswith("[") else [text]
        except json.JSONDecodeError:
            parsed = [text]
        raw = parsed if isinstance(parsed, list) else [parsed]
    result = []
    for name in raw:
        name = str(name)
        if name not in result and safe_world_path(name):
            result.append(name)
    return result


def encode_world_files(names: list[str]) -> str:
    return json.dumps(list(dict.fromkeys(names)), ensure_ascii=False, separators=(",", ":")) if names else ""


def build_world_info(
    world_names: str | list[str], context: str, fields: dict[str, str], user_name: str = _config.DEFAULT_USER_NAME
) -> str:
    """Activate basic SillyTavern World Info entries by key and secondary key."""
    sections = []
    for world_name in active_world_files(world_names):
        path = safe_world_path(world_name)
        if path is None:
            continue
        try:
            data = cached_json(path)
            raw_entries = data.get("entries", {})
            entries = list(raw_entries.values()) if isinstance(raw_entries, dict) else list(raw_entries or [])
            lowered = context.casefold()
            activated = []
            activated_ids = set()
            for _ in range(3):
                changed = False
                for entry_index, entry in enumerate(entries):
                    if entry_index in activated_ids or entry.get("disable"):
                        continue
                    keys = entry.get("key", [])
                    secondary = entry.get("keysecondary", [])
                    if isinstance(keys, str):
                        keys = [keys]
                    if isinstance(secondary, str):
                        secondary = [secondary]
                    key_hit = bool(entry.get("constant")) or any(
                        str(k).casefold() in lowered for k in keys if str(k).strip()
                    )
                    if not key_hit:
                        continue
                    if secondary and not any(str(k).casefold() in lowered for k in secondary if str(k).strip()):
                        continue
                    content = str(entry.get("content") or "").strip()
                    if content:
                        activated.append((int(entry.get("order", 100)), content))
                        activated_ids.add(entry_index)
                        lowered += "\n" + content.casefold()
                        changed = True
                if not changed:
                    break
            activated.sort(key=lambda item: item[0])
            sections.extend(replace_macros(content, fields, user_name) for _, content in activated)
        except Exception:
            logging.warning("Could not load World Info %s", world_name, exc_info=True)
    return "\n\n".join(sections)[:12000]


def _prompt_catalog_label(stem: str) -> str:
    return stem.replace("_", " ").replace("-", " ").title()


def _merge_system_prompt_file(result: dict[str, dict[str, str]], path: Path) -> None:
    if path.suffix.casefold() == ".txt":
        _merge_system_prompt_text(result, path)
    else:
        _merge_system_prompt_json(result, path)


def _merge_system_prompt_json(result: dict[str, dict[str, str]], path: Path) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logging.warning("Could not read System Prompt catalog %s", path, exc_info=True)
        return
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        result[path.stem] = {"name": _prompt_catalog_label(path.stem), "prompt": "\n".join(raw)}
        return
    if isinstance(raw, str) and raw.strip():
        result[path.stem] = {"name": _prompt_catalog_label(path.stem), "prompt": raw}
        return
    if isinstance(raw, dict):
        prompt = str(raw.get("prompt") or raw.get("content") or "")
        if prompt.strip():
            key = str(raw.get("id") or path.stem)
            result[key] = {"name": str(raw.get("name") or path.stem), "prompt": prompt}
            return
    for key, value in raw.items() if isinstance(raw, dict) else []:
        if str(key) in {"id", "name", "prompt", "content", "post_history"}:
            continue
        if isinstance(value, str):
            result[str(key)] = {"name": str(key), "prompt": value}
        elif isinstance(value, dict) and str(value.get("prompt") or "").strip():
            result[str(key)] = {"name": str(value.get("name") or key), "prompt": str(value["prompt"])}


def _merge_system_prompt_text(result: dict[str, dict[str, str]], path: Path) -> None:
    try:
        prompt = path.read_text(encoding="utf-8")
    except OSError:
        logging.warning("Could not read System Prompt text file %s", path, exc_info=True)
        return
    if prompt.strip():
        result[path.stem] = {"name": _prompt_catalog_label(path.stem), "prompt": prompt}


def load_system_prompts() -> dict[str, dict[str, str]]:
    result = {}
    if _config.SYSTEM_PROMPTS_DIR.exists():
        for path in sorted(
            list(_config.SYSTEM_PROMPTS_DIR.glob("*.json")) + list(_config.SYSTEM_PROMPTS_DIR.glob("*.txt"))
        ):
            _merge_system_prompt_file(result, path)
    return dict(list(result.items())[: _config.CATALOG_MAX_ITEMS])


def get_system_prompt_choice(name: str) -> str | None:
    prompts = load_system_prompts()
    item = prompts.get(name)
    if item is None and str(name).startswith("id:"):
        item = next((value for key, value in prompts.items() if system_prompt_callback_token(key) == name), None)
    return item["prompt"] if item else None


def system_prompt_label(prompt: str | None) -> str:
    """Return only the selected native prompt label, never its body."""
    value = str(prompt or "").strip()
    if not value:
        return "off"
    for item in load_system_prompts().values():
        if str(item.get("prompt") or "") == value:
            return panel_label(str(item.get("name") or "custom"), 64)
    return "custom"


def system_prompt_callback_token(key: str) -> str:
    candidate = "systemprompt:" + str(key)
    if len(candidate.encode("utf-8")) <= 64:
        return str(key)
    return "id:" + hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:24]


def system_prompt_choices() -> list[tuple[str, str]]:
    return [(key, item["name"]) for key, item in load_system_prompts().items()]


def replace_macros(text: str, fields: dict[str, str], user_name: str = _config.DEFAULT_USER_NAME) -> str:
    result = (
        text.replace("{{char}}", fields["name"])
        .replace("{{user}}", user_name)
        .replace("<USER>", user_name)
        .replace("<BOT>", fields["name"])
    )

    def pick_macro(match):
        choices = [item for item in match.group(1).split("::") if item]
        return random.choice(choices) if choices else ""  # noqa: S311 -- character text selection, not a security token

    result = re.sub(r"\{\{(?:random|pick)::([^}]+)\}\}", pick_macro, result)
    now = time.localtime()
    return (
        result.replace("{{time}}", time.strftime("%H:%M", now))
        .replace("{{date}}", time.strftime("%Y-%m-%d", now))
        .replace("{{weekday}}", time.strftime("%A", now))
    )


def build_system_prompt(fields: dict[str, str], user_name: str = _config.DEFAULT_USER_NAME) -> str:
    source = "\x1f".join(
        str(fields.get(key) or "")
        for key in ("system_prompt", "description", "personality", "scenario", "mes_example", "name")
    )
    if any(token in source for token in ("{{random", "{{pick", "{{time}}", "{{date}}", "{{weekday}}")):
        return _build_system_prompt_uncached(fields, user_name)
    key = hashlib.sha256((source + "\x1f" + user_name).encode("utf-8")).hexdigest()
    return cached_text("system-prompt:" + key, lambda: _build_system_prompt_uncached(fields, user_name))


def _build_system_prompt_uncached(fields: dict[str, str], user_name: str = _config.DEFAULT_USER_NAME) -> str:
    system = fields["system_prompt"] or (
        "Write {{char}}'s next reply in a fictional chat between {{char}} and {{user}}. "
        "Stay in character and do not speak for {{user}}."
    )
    sections = [replace_macros(system, fields, user_name)]
    for label, key in (
        ("Character description", "description"),
        ("Personality", "personality"),
        ("Scenario", "scenario"),
    ):
        if fields[key]:
            sections.append(f"\n## {label}\n{replace_macros(fields[key], fields, user_name)}")
    if fields["mes_example"]:
        examples = fields["mes_example"][-8000:]
        sections.append(f"\n## Example dialogue\n{replace_macros(examples, fields, user_name)}")
    return "\n".join(sections)


def character_display_name(path: Path) -> str:
    """Return the embedded card name with a safe filename fallback."""
    try:
        return str(card_fields(read_png_chara(path)).get("name") or path.stem)
    except Exception:
        return path.stem
