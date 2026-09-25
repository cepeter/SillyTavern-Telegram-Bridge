"""Canonical bot commands owner."""

from __future__ import annotations

import logging

from bridge.telegram import telegram_request


def set_bot_commands(token: str) -> None:
    try:
        telegram_request(
            token,
            "setMyCommands",
            {
                "commands": [
                    {"command": "start", "description": "Send greeting or show missing setup"},
                    {"command": "help", "description": "Browse the interactive command guide"},
                    {"command": "cancel", "description": "Cancel the current pending input"},
                    {"command": "providers", "description": "Choose Story or Utility provider/model"},
                    {"command": "character", "description": "Open character management panel"},
                    {"command": "session", "description": "Manage sessions; delete inactive only"},
                    {"command": "sync", "description": "Open Live API Sync controls"},
                    {"command": "update", "description": "Check and confirm bridge update"},
                    {"command": "persona", "description": "Choose user persona"},
                    {"command": "world", "description": "Choose World Info lore"},
                    {"command": "status", "description": "Show read-only session status"},
                    {"command": "edit", "description": "Edit last user message"},
                    {"command": "voice", "description": "Open automatic voice panel"},
                    {"command": "voice_input", "description": "Open transcription/model/language panel"},
                    {"command": "settings", "description": "Open generation settings panel"},
                    {"command": "stream", "description": "Open streaming on/off panel"},
                    {"command": "preset", "description": "Open preset use/delete panel"},
                    {"command": "macro", "description": "Preview a macro"},
                    {"command": "stscript", "description": "Open allowlisted STscript actions"},
                    {"command": "memory", "description": "Open active-session memory controls"},
                    {"command": "remember", "description": "Store an explicit memory"},
                    {"command": "summarize", "description": "Confirm active-session summary regeneration"},
                    {"command": "databank", "description": "Open RAG/list/remove panel"},
                    {"command": "group", "description": "Open Forum Topic group controls"},
                    {"command": "new", "description": "Name and start a new session"},
                    {"command": "reset", "description": "Confirm active-session reset"},
                    {"command": "regen", "description": "Regenerate last response"},
                    {"command": "swipe", "description": "Browse response variants"},
                    {"command": "branch", "description": "Choose an active chat branch"},
                    {"command": "prompt", "description": "Inspect assembled prompt diagnostics"},
                    {"command": "continue", "description": "Continue last response"},
                    {"command": "retry", "description": "Retry the last failed response"},
                    {"command": "note", "description": "Open Author's Note panel"},
                    {"command": "systemprompt", "description": "Choose native JSON/TXT System Prompt"},
                    {"command": "language", "description": "Choose model reply language"},
                    {"command": "expression", "description": "Choose manual or automatic character expressions"},
                    {"command": "imagine", "description": "Generate an image from a prompt"},
                    {"command": "scene", "description": "Open structured scene-state controls"},
                ]
            },
        )
    except Exception:
        logging.warning("Could not register Telegram command menu", exc_info=True)
