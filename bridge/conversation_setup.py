"""Actor-scoped conversation setup; only final Apply mutates a target session."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass

from bridge.card_content import encode_world_files, get_system_prompt_choice, safe_character_path, safe_world_path
from bridge.conversation_lifecycle import (
    configure_conversation,
    conversation_state,
    initialize_conversation,
    is_group_conversation,
)
from bridge.limits import PENDING_SETTINGS_TTL_SECONDS
from bridge.metadata import get_meta, set_meta
from bridge.persona_service import PersonaService
from bridge.session_repository import insert_session_row, load_session_row, update_session_row
from bridge.session_titles import normalize_session_title
from bridge.settings import AppSettings
from bridge.sqlite_store import write_transaction


def setup_key(chat_id: str, actor_id: str) -> str:
    return f"conversation_setup:{chat_id}:{actor_id}"


def begin_setup(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict,
    actor_id: str,
    filename: str,
    *,
    app_settings: AppSettings,
) -> dict:
    path = safe_character_path(filename, app_settings=app_settings)
    if not actor_id or path is None or path.is_symlink():
        raise ValueError("Character choice expired")
    state = {
        "session_id": session["session_id"],
        "source_epoch": conversation_state(db, chat_id, session["session_id"]).epoch,
        "actor_id": actor_id,
        "character_file": path.name,
        "character_digest": hashlib.sha256(path.read_bytes()).hexdigest(),
        "nonce": secrets.token_urlsafe(12),
        "stage": "mode",
        "conversation_mode": "normal",
        "lightnovel_strategy": "",
        "persona_id": "",
        "world_files": [],
        "system_prompt_key": "",
        "target_session_id": "",
        "new_title": "",
        "page": 0,
        "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS,
    }
    set_meta(db, setup_key(chat_id, actor_id), json.dumps(state, ensure_ascii=False))
    return state


@dataclass(frozen=True)
class ConversationSetupService:
    app_settings: AppSettings
    persona_service: PersonaService

    def begin(self, db: sqlite3.Connection, chat_id: str, session: dict, actor_id: str, filename: str) -> dict:
        return begin_setup(db, chat_id, session, actor_id, filename, app_settings=self.app_settings)

    def load(self, db: sqlite3.Connection, chat_id: str, session_id: str, actor_id: str, nonce: str = "") -> dict:
        try:
            state = json.loads(get_meta(db, setup_key(chat_id, actor_id), "") or "{}")
            valid = (
                isinstance(state, dict)
                and state.get("actor_id") == actor_id
                and state.get("session_id") == session_id
                and float(state.get("expires_at") or 0) > time.time()
                and (not nonce or state.get("nonce") == nonce)
                and state.get("source_epoch") == conversation_state(db, chat_id, session_id).epoch
                and get_meta(db, f"active_session:{chat_id}", "default") == session_id
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("Setup expired; run /character again.")
        return state

    def _save(self, db: sqlite3.Connection, chat_id: str, actor_id: str, state: dict) -> dict:
        set_meta(db, setup_key(chat_id, actor_id), json.dumps(state, ensure_ascii=False))
        return state

    def choose(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session: dict,
        actor_id: str,
        nonce: str,
        stage: str,
        action: str,
        value: str = "",
    ) -> dict:
        with write_transaction(db):
            state = self.load(db, chat_id, session["session_id"], actor_id, nonce)
            if state["stage"] != stage:
                raise ValueError("This setup step is no longer active")
            if stage == "mode" and action == "pick":
                if value not in {"normal", "lightnovel"}:
                    raise ValueError("Choose Normal or Light Novel")
                state["conversation_mode"] = value
                state["lightnovel_strategy"] = ""
                state["stage"] = "strategy" if value == "lightnovel" else "persona"
            elif stage == "strategy" and action == "pick":
                if value not in {"a", "b", "c"}:
                    raise ValueError("Choose A, B or C")
                state["lightnovel_strategy"] = value
                state["stage"] = "persona"
            elif stage == "persona" and action == "pick":
                if value and self.persona_service.get(value) is None:
                    raise ValueError("Persona is unavailable")
                state["persona_id"] = value
                state["stage"] = "world"
            elif stage == "world":
                if action == "off":
                    state["world_files"] = []
                    state["stage"] = "system_prompt"
                elif action == "next":
                    state["stage"] = "system_prompt"
                elif action == "pick" and safe_world_path(value, app_settings=self.app_settings):
                    worlds = list(state["world_files"])
                    if value in worlds:
                        worlds.remove(value)
                    else:
                        worlds.append(value)
                    state["world_files"] = worlds
                else:
                    raise ValueError("World choice is unavailable")
            elif stage == "system_prompt" and action == "pick":
                if value and get_system_prompt_choice(value, app_settings=self.app_settings) is None:
                    raise ValueError("System Prompt is unavailable")
                state["system_prompt_key"] = value
                state["stage"] = "session"
            elif stage == "session" and action == "pick":
                if value == "$new":
                    state["target_session_id"] = "$new"
                    state["stage"] = "session_name"
                else:
                    if load_session_row(db, chat_id, value) is None or is_group_conversation(db, chat_id, value):
                        raise ValueError("Choose a standard session")
                    if conversation_state(db, chat_id, value).started:
                        raise ValueError("This session has already started. Use /reset or /new first.")
                    state["target_session_id"] = value
                    state["stage"] = "review"
            else:
                raise ValueError("Invalid setup action")
            state["page"] = 0
            return self._save(db, chat_id, actor_id, state)

    def set_title(self, db: sqlite3.Connection, chat_id: str, session: dict, actor_id: str, title: str) -> dict:
        title = normalize_session_title(title)
        with write_transaction(db):
            state = self.load(db, chat_id, session["session_id"], actor_id)
            if state["stage"] != "session_name":
                raise ValueError("Setup is not waiting for a session name")
            state.update(new_title=title, stage="review")
            return self._save(db, chat_id, actor_id, state)

    def back(self, db: sqlite3.Connection, chat_id: str, session: dict, actor_id: str, nonce: str) -> dict:
        with write_transaction(db):
            state = self.load(db, chat_id, session["session_id"], actor_id, nonce)
            stages = (
                ["mode"]
                + (["strategy"] if state["conversation_mode"] == "lightnovel" else [])
                + ["persona", "world", "system_prompt", "session", "review"]
            )
            current = state["stage"]
            state["stage"] = "session" if current == "session_name" else stages[max(0, stages.index(current) - 1)]
            state["page"] = 0
            return self._save(db, chat_id, actor_id, state)

    def cancel(self, db: sqlite3.Connection, chat_id: str, session: dict, actor_id: str, nonce: str) -> None:
        with write_transaction(db):
            self.load(db, chat_id, session["session_id"], actor_id, nonce)
            set_meta(db, setup_key(chat_id, actor_id), "")

    def apply(self, db: sqlite3.Connection, chat_id: str, session: dict, actor_id: str, nonce: str) -> dict:
        with write_transaction(db):
            state = self.load(db, chat_id, session["session_id"], actor_id, nonce)
            if state["stage"] != "review":
                raise ValueError("Complete all setup steps before Apply")
            path = safe_character_path(state["character_file"], app_settings=self.app_settings)
            if (
                path is None
                or path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != state["character_digest"]
            ):
                raise ValueError("Character changed; run /character again.")
            persona = state["persona_id"]
            if persona and self.persona_service.get(persona) is None:
                raise ValueError("Persona is unavailable")
            worlds = state["world_files"]
            if any(safe_world_path(name, app_settings=self.app_settings) is None for name in worlds):
                raise ValueError("World is unavailable")
            prompt_key = state["system_prompt_key"]
            prompt = get_system_prompt_choice(prompt_key, app_settings=self.app_settings) if prompt_key else ""
            if prompt is None:
                raise ValueError("System Prompt is unavailable")
            target_id = state["target_session_id"]
            if target_id == "$new":
                target_id = "setup-" + nonce
                insert_session_row(
                    db,
                    {
                        "chat_id": chat_id,
                        "session_id": target_id,
                        "title": normalize_session_title(state["new_title"]),
                        "character_file": path.name,
                        "model_id": session["model_id"],
                        "persona_id": persona,
                        "world_file": encode_world_files(worlds),
                        "author_note": "",
                        "system_prompt": prompt,
                        "response_language": "auto",
                    },
                    time.time(),
                )
                initialize_conversation(db, chat_id, target_id)
            target = load_session_row(db, chat_id, target_id)
            if target is None or is_group_conversation(db, chat_id, target_id):
                raise ValueError("Choose a standard session")
            configure_conversation(db, chat_id, target_id, state["conversation_mode"], state["lightnovel_strategy"])
            update_session_row(
                db,
                chat_id,
                target_id,
                {
                    "character_file": path.name,
                    "persona_id": persona,
                    "world_file": encode_world_files(worlds),
                    "system_prompt": prompt,
                },
                time.time(),
            )
            set_meta(db, f"active_session:{chat_id}", target_id)
            set_meta(db, setup_key(chat_id, actor_id), "")
            return load_session_row(db, chat_id, target_id) or target
