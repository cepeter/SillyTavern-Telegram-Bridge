"""Budget-aware prompt compaction for long-running sessions.

The compactor never rewrites the current user turn or the fixed character/system
instructions. It first drops oldest conversational history, then shrinks
retrieved RAG/memory context, then the continuity summary. If the fixed prompt
alone exceeds the configured budget it reports that condition rather than
silently truncating character instructions.
"""
from __future__ import annotations

import copy
import math
import os
import re


DEFAULT_CONTEXT_WINDOW_TOKENS = 32768
DEFAULT_CONTEXT_OUTPUT_RESERVE_TOKENS = 4096
DEFAULT_CONTEXT_HISTORY_CANDIDATES = 96
MIN_CONTEXT_INPUT_BUDGET_TOKENS = 2048


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def context_window_tokens() -> int:
    return _bounded_env_int(
        "SILLYTAVERN_CONTEXT_WINDOW_TOKENS",
        DEFAULT_CONTEXT_WINDOW_TOKENS,
        4096,
        1_000_000,
    )


def context_output_reserve_tokens() -> int:
    return _bounded_env_int(
        "SILLYTAVERN_CONTEXT_OUTPUT_RESERVE_TOKENS",
        DEFAULT_CONTEXT_OUTPUT_RESERVE_TOKENS,
        512,
        131072,
    )


def context_input_budget_tokens() -> int:
    return max(
        MIN_CONTEXT_INPUT_BUDGET_TOKENS,
        context_window_tokens() - context_output_reserve_tokens(),
    )


def context_history_candidate_limit() -> int:
    return _bounded_env_int(
        "SILLYTAVERN_CONTEXT_HISTORY_CANDIDATES",
        DEFAULT_CONTEXT_HISTORY_CANDIDATES,
        8,
        512,
    )


def _content_tokens(content) -> int:
    if isinstance(content, str):
        return max(1, math.ceil(len(content) / 4))
    if isinstance(content, list):
        total = 0
        for item in content:
            if not isinstance(item, dict):
                total += _content_tokens(str(item))
                continue
            if item.get("type") == "image_url":
                # Do not count a base64 data URI as text. Reserve a conservative
                # fixed amount; providers account for image tokens differently.
                total += 1024
            else:
                total += _content_tokens(item.get("text") or "")
        return total
    return _content_tokens(str(content or ""))


def estimate_message_tokens(messages: list[dict]) -> int:
    return sum(8 + _content_tokens(message.get("content", "")) for message in messages) + 16


def _replace_text_content(message: dict, text: str) -> None:
    content = message.get("content")
    if isinstance(content, str):
        message["content"] = text
        return
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                item["text"] = text
                return


def _text_content(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                return str(item.get("text") or "")
    return ""


def _trim_middle(value: str, maximum: int) -> str:
    value = str(value or "")
    if maximum <= 0:
        return ""
    if len(value) <= maximum:
        return value
    if maximum < 80:
        return value[:maximum]
    head = maximum * 2 // 3
    tail = maximum - head - 18
    return value[:head].rstrip() + "\n…[compacted]…\n" + value[-max(0, tail):].lstrip()


def _shrink_tagged_section(text: str, tag: str, target_chars: int) -> tuple[str, bool]:
    pattern = re.compile(
        rf"(<{re.escape(tag)}>\n?)(.*?)(\n?</{re.escape(tag)}>)",
        flags=re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return text, False
    body = match.group(2)
    trimmed = _trim_middle(body, max(0, int(target_chars)))
    if trimmed == body:
        return text, False
    replacement = match.group(1) + trimmed + match.group(3)
    return text[:match.start()] + replacement + text[match.end():], True


def _shrink_summary_section(text: str, target_chars: int) -> tuple[str, bool]:
    marker = "## Session continuity summary\n"
    start = text.find(marker)
    if start < 0:
        return text, False
    body_start = start + len(marker)
    next_section = text.find("\n\n## ", body_start)
    body_end = len(text) if next_section < 0 else next_section
    body = text[body_start:body_end]
    trimmed = _trim_middle(body, max(0, int(target_chars)))
    if trimmed == body:
        return text, False
    return text[:body_start] + trimmed + text[body_end:], True


def compact_chat_messages(
    messages: list[dict],
    budget_tokens: int | None = None,
    *,
    min_recent_messages: int = 6,
) -> tuple[list[dict], dict[str, int | bool]]:
    """Compact a built prompt while preserving fixed instructions/current turn."""
    budget = max(
        MIN_CONTEXT_INPUT_BUDGET_TOKENS,
        int(budget_tokens or context_input_budget_tokens()),
    )
    compacted = copy.deepcopy(messages)
    original_tokens = estimate_message_tokens(compacted)
    dropped_history = 0
    rag_trimmed = memory_trimmed = summary_trimmed = False

    def current_tokens() -> int:
        return estimate_message_tokens(compacted)

    if original_tokens <= budget:
        return compacted, {
            "budget_tokens": budget,
            "original_tokens": original_tokens,
            "final_tokens": original_tokens,
            "dropped_history": 0,
            "rag_trimmed": False,
            "memory_trimmed": False,
            "summary_trimmed": False,
            "over_budget": False,
        }

    # Preserve all system messages and the newest non-system message. Drop old
    # user/assistant turns first, keeping a recent conversational floor.
    conversation_indices = [
        index for index, message in enumerate(compacted)
        if message.get("role") in {"user", "assistant"}
    ]
    protected_latest = conversation_indices[-1] if conversation_indices else -1
    removable = [
        index for index in conversation_indices
        if index != protected_latest
    ]
    keep_floor = max(0, int(min_recent_messages) - 1)
    while current_tokens() > budget and len(removable) > keep_floor:
        index = removable.pop(0)
        compacted[index]["_drop_for_context"] = True
        dropped_history += 1
    compacted = [message for message in compacted if not message.pop("_drop_for_context", False)]

    # Trim untrusted retrieved context before touching continuity summary.
    if current_tokens() > budget and compacted:
        latest = next(
            (message for message in reversed(compacted) if message.get("role") == "user"),
            compacted[-1],
        )
        text = _text_content(latest)
        for tag, floor in (("untrusted_data_bank_references", 512), ("untrusted_memory", 512)):
            if current_tokens() <= budget:
                break
            pattern = re.search(
                rf"<{tag}>\n?(.*?)\n?</{tag}>",
                text,
                flags=re.DOTALL,
            )
            if not pattern:
                continue
            body_len = len(pattern.group(1))
            deficit_chars = max(0, (current_tokens() - budget) * 4)
            target = max(floor, body_len - deficit_chars - 256)
            text, changed = _shrink_tagged_section(text, tag, target)
            if changed:
                _replace_text_content(latest, text)
                if tag == "untrusted_data_bank_references":
                    rag_trimmed = True
                else:
                    memory_trimmed = True

    # If still over budget, allow history to shrink to the latest pair.
    if current_tokens() > budget:
        conversation_indices = [
            index for index, message in enumerate(compacted)
            if message.get("role") in {"user", "assistant"}
        ]
        protected_latest = conversation_indices[-1] if conversation_indices else -1
        removable = [index for index in conversation_indices if index != protected_latest]
        while current_tokens() > budget and len(removable) > 1:
            index = removable.pop(0)
            compacted[index]["_drop_for_context"] = True
            dropped_history += 1
        compacted = [message for message in compacted if not message.pop("_drop_for_context", False)]

    # Continuity summary is valuable, so compact it only after history and
    # retrieval context have already been reduced.
    if current_tokens() > budget:
        system_message = next(
            (message for message in compacted if message.get("role") == "system"),
            None,
        )
        if system_message is not None:
            text = _text_content(system_message)
            marker = "## Session continuity summary\n"
            start = text.find(marker)
            if start >= 0:
                body_start = start + len(marker)
                next_section = text.find("\n\n## ", body_start)
                body_end = len(text) if next_section < 0 else next_section
                body_len = body_end - body_start
                deficit_chars = max(0, (current_tokens() - budget) * 4)
                target = max(1000, body_len - deficit_chars - 256)
                text, changed = _shrink_summary_section(text, target)
                if changed:
                    _replace_text_content(system_message, text)
                    summary_trimmed = True

    final_tokens = current_tokens()
    return compacted, {
        "budget_tokens": budget,
        "original_tokens": original_tokens,
        "final_tokens": final_tokens,
        "dropped_history": dropped_history,
        "rag_trimmed": rag_trimmed,
        "memory_trimmed": memory_trimmed,
        "summary_trimmed": summary_trimmed,
        "over_budget": final_tokens > budget,
    }
