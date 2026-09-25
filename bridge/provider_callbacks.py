"""Canonical provider callbacks owner."""

from __future__ import annotations

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.callbacks import remove_inline_keyboard
from bridge.model_selection import (
    clear_model_target_selection,
    get_model_target_selection,
    set_model_target_selection,
    set_task_model,
    task_model_for_session,
)
from bridge.provider_discovery import refresh_model_catalog
from bridge.provider_panels import send_model_menu, send_model_target_menu, send_provider_health_menu
from bridge.session_core import update_session
from bridge.telegram import send_text


def handle_provider_model_callback(
    db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id, *, request_context
):
    """Handle provider, model, and catalog-only callbacks."""
    message_id = message.get("message_id")
    if data.startswith("models:providers:"):
        page = int(data.rsplit(":", 1)[1])
        answer_callback(token, str(callback.get("id", "")), "Page")
        send_model_menu(
            token,
            chat_id,
            session["model_id"] or request_context.app_settings.default_model,
            message_id=message_id,
            page=page,
            request_context=request_context,
        )
        return True
    if data.startswith("models:model:"):
        parts = data.split(":")
        provider_id = resolve_dynamic_callback_token(parts[2], "provider", chat_id, db=db) or ""
        page = int(parts[3])
        answer_callback(token, str(callback.get("id", "")), "Page")
        send_model_menu(
            token,
            chat_id,
            session["model_id"] or request_context.app_settings.default_model,
            provider_id,
            message_id,
            page,
            request_context=request_context,
        )
        return True
    if data == "models:target":
        answer_callback(token, str(callback.get("id", "")), "Back to target")
        send_model_target_menu(
            token,
            chat_id,
            session["model_id"] or request_context.app_settings.default_model,
            task_model_for_session(db, chat_id, session, "utility", app_settings=request_context.app_settings),
            message_id,
            request_context=request_context,
        )
        return True
    if data == "models:cancel":
        answer_callback(token, str(callback.get("id", "")), "Cancelled")
        remove_inline_keyboard(db, token, callback)
        return True
    if data == "models:back":
        answer_callback(token, str(callback.get("id", "")), "Back to providers")
        send_model_menu(
            token,
            chat_id,
            session["model_id"] or request_context.app_settings.default_model,
            message_id=message_id,
            request_context=request_context,
        )
        return True
    if data == "provider:health":
        answer_callback(token, str(callback.get("id", "")), "Health")
        send_provider_health_menu(token, chat_id, message_id, request_context=request_context)
        return True
    if data == "provider:refresh":
        _config, refreshed, failed = refresh_model_catalog(force=True, app_settings=request_context.app_settings)
        answer_callback(token, str(callback.get("id", "")), "Refreshed")
        send_text(token, chat_id, f"Model catalog refreshed: {refreshed} providers updated; {failed} failed.")
        send_model_menu(
            token,
            chat_id,
            session["model_id"] or request_context.app_settings.default_model,
            message_id=message_id,
            request_context=request_context,
        )
        return True
    if data == "provider:back":
        send_model_menu(
            token,
            chat_id,
            session["model_id"] or request_context.app_settings.default_model,
            message_id=message_id,
            request_context=request_context,
        )
        return True
    if data.startswith("provider:"):
        provider_id = resolve_dynamic_callback_token(data.split(":", 1)[1], "provider", chat_id, db=db) or ""
        answer_callback(token, str(callback.get("id", "")), "Provider selected")
        send_model_menu(
            token,
            chat_id,
            session["model_id"] or request_context.app_settings.default_model,
            provider_id,
            message_id=message_id,
            request_context=request_context,
        )
        return True
    if data.startswith("unsupported:"):
        provider_id = resolve_dynamic_callback_token(data.split(":", 1)[1], "provider", chat_id, db=db) or ""
        answer_callback(token, str(callback.get("id", "")), "Catalog only: adapter not enabled")
        send_text(
            token,
            chat_id,
            f"Provider '{provider_id}' is visible in the bridge catalog, but its adapter is not enabled yet.",
        )
        return True
    if data.startswith("modeltarget:"):
        target = data.split(":", 1)[1]
        if target not in {"story", "utility"}:
            answer_callback(token, str(callback.get("id", "")), "Model target invalid")
            return True
        set_model_target_selection(db, chat_id, session_id, target)
        answer_callback(token, str(callback.get("id", "")), "Target selected")
        send_model_menu(
            token,
            chat_id,
            session["model_id"] or request_context.app_settings.default_model,
            message_id=message_id,
            request_context=request_context,
        )
        return True
    if not data.startswith("model:"):
        return False
    model = resolve_dynamic_callback_token(data.split(":", 1)[1], "model", chat_id, db=db) or ""
    if not model:
        answer_callback(token, str(callback.get("id", "")), "Model choice expired")
        return True
    target = get_model_target_selection(db, chat_id, session_id)
    if not target:
        answer_callback(token, str(callback.get("id", "")), "Choose Story or Utility first")
        send_model_target_menu(
            token,
            chat_id,
            session["model_id"] or request_context.app_settings.default_model,
            task_model_for_session(db, chat_id, session, "utility", app_settings=request_context.app_settings),
            message_id,
            request_context=request_context,
        )
        return True
    if target == "story":
        update_session(
            db, chat_id, session_id, operation_id=operation_id, operation_kind="model_select", model_id=model
        )
        message = f"Story model updated: {model}"
    else:
        set_task_model(db, chat_id, session_id, model, "utility")
        message = f"Utility model updated: {model}"
    clear_model_target_selection(db, chat_id, session_id)
    answer_callback(token, str(callback.get("id", "")), "Model updated")
    send_text(token, chat_id, message)
    send_model_target_menu(
        token,
        chat_id,
        model if target == "story" else session["model_id"] or request_context.app_settings.default_model,
        task_model_for_session(db, chat_id, session, "utility", app_settings=request_context.app_settings),
        message_id,
        request_context=request_context,
    )
    return True
