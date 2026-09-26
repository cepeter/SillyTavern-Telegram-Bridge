"""Conversation setup selector UI; no session mutation while browsing."""

from __future__ import annotations

import json

from bridge.callback_tokens import dynamic_callback_token
from bridge.card_content import card_fields_from_file, system_prompt_choices, world_file_paths
from bridge.conversation_lifecycle import conversation_state, is_group_conversation
from bridge.panel_utils import panel_label, panel_page
from bridge.persona_service import PersonaService
from bridge.request_types import RequestContext
from bridge.session_repository import list_session_rows
from bridge.telegram import send_panel_request

STRATEGY_LABELS = {"a": "A — Story Inline", "b": "B — Utility Model", "c": "C — Story Second Pass"}


def _button(label: str, action: str, value: str, chat_id: str, state: dict, context: RequestContext) -> dict:
    encoded = json.dumps({"nonce": state["nonce"], "stage": state["stage"], "action": action, "value": value})
    token = dynamic_callback_token("conversation_setup", encoded, chat_id, db=context.db)
    return {"text": panel_label(label), "callback_data": "setup:" + token}


def send_setup_panel(
    token: str,
    chat_id: str,
    state: dict,
    message_id: int | None = None,
    *,
    request_context: RequestContext,
    persona_service: PersonaService | None = None,
) -> None:
    settings = request_context.app_settings
    stage = state["stage"]
    options: list[tuple[str, str]] = []
    if stage == "mode":
        options = [("normal", "Normal"), ("lightnovel", "Light Novel")]
    elif stage == "strategy":
        options = list(STRATEGY_LABELS.items())
    elif stage == "persona":
        if persona_service is None:
            raise ValueError("Persona catalog is unavailable")
        options = [(key, str(value.get("name") or key)) for key, value in persona_service.list().items()]
    elif stage == "world":
        options = [
            (path.name, ("✓ " if path.name in state["world_files"] else "") + path.stem)
            for path in world_file_paths(app_settings=settings)
        ]
    elif stage == "system_prompt":
        options = system_prompt_choices(app_settings=settings)
    elif stage == "session":
        options = [
            (item["session_id"], item["title"])
            for item in list_session_rows(request_context.db, chat_id)
            if not conversation_state(request_context.db, chat_id, item["session_id"]).started
            and not is_group_conversation(request_context.db, chat_id, item["session_id"])
        ]
    selected, page, pages = panel_page(options, int(state.get("page") or 0))
    rows = [[_button(label, "pick", value, chat_id, state, request_context)] for value, label in selected]
    navigation = []
    if page:
        navigation.append(_button("Previous", "page", str(page - 1), chat_id, state, request_context))
    if page + 1 < pages:
        navigation.append(_button("Next page", "page", str(page + 1), chat_id, state, request_context))
    if navigation:
        rows.append(navigation)
    if stage in {"persona", "system_prompt"}:
        rows.append([_button("Off / Skip", "pick", "", chat_id, state, request_context)])
    elif stage == "world":
        rows.append(
            [
                _button("Off / Skip", "off", "", chat_id, state, request_context),
                _button("Next", "next", "", chat_id, state, request_context),
            ]
        )
    elif stage == "session":
        rows.append([_button("Create new session", "pick", "$new", chat_id, state, request_context)])
    elif stage == "review":
        rows.append([_button("Apply setup", "apply", "", chat_id, state, request_context)])
    rows.append(
        [
            _button("Back", "back", "", chat_id, state, request_context),
            _button("Cancel", "cancel", "", chat_id, state, request_context),
        ]
    )
    name = card_fields_from_file(state["character_file"], app_settings=settings)["name"]
    text = f"Conversation setup — {name}\nStep: {stage.replace('_', ' ').title()}\n"
    if stage == "strategy":
        text += "\nA: story + choices in one request.\nB: choices from the Utility model.\nC: a second Story-model pass.\nEach turn offers 2–4 choices."
    elif stage == "session":
        text += "\nChoose an unstarted standard session or create a new session. Existing stories require /reset first."
    elif stage == "session_name":
        text += "\nPlease enter the new session name now. Send /cancel to cancel."
    elif stage == "review":
        mode = state["conversation_mode"]
        strategy = STRATEGY_LABELS.get(state["lightnovel_strategy"], "—")
        target = state["new_title"] if state["target_session_id"] == "$new" else state["target_session_id"]
        text += (
            f"\nMode: {mode}\nStrategy: {strategy}\nPersona: {state['persona_id'] or 'Off'}"
            f"\nWorld: {', '.join(state['world_files']) or 'Off'}"
            f"\nSystem Prompt: {state['system_prompt_key'] or 'Off'}\nSession: {target}"
            "\n\nApply changes together. The story does not start until /start."
        )
    else:
        text += "\nNo changes are applied to the session until the final Apply."
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": rows}}
    if message_id is not None:
        payload["message_id"] = message_id
    try:
        send_panel_request(
            token,
            "editMessageText" if message_id is not None else "sendMessage",
            payload,
            request_context=request_context,
        )
    except RuntimeError as exc:
        if "not modified" not in str(exc).casefold():
            raise
