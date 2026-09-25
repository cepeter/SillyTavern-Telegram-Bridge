"""Canonical provider panels owner."""

from __future__ import annotations

import logging

from bridge.callback_tokens import dynamic_callback_token
from bridge.cards import send_panel_message
from bridge.panel_utils import panel_label, panel_page
from bridge.provider_discovery import get_model_groups, provider_health_checks


def send_model_menu(
    token: str,
    chat_id: str,
    current_model: str,
    provider_id: str | None = None,
    message_id: int | None = None,
    page: int = 0,
    *,
    request_context,
) -> None:
    groups = get_model_groups(app_settings=request_context.app_settings)
    if provider_id is None:
        options = [
            (group_id, f"{label} ({len(models)}){'' if is_supported else ' · catalog only'}", models, is_supported)
            for group_id, (label, models, is_supported) in groups.items()
        ]
        page_options, current_page, total_pages = panel_page(options, page)
        rows = []
        for group_id, label, models, _is_supported in page_options:
            mark = "✅ " if any(model_id == current_model for _, model_id in models) else ""
            rows.append(
                [
                    {
                        "text": mark + panel_label(label),
                        "callback_data": "provider:"
                        + dynamic_callback_token("provider", group_id, chat_id, db=request_context.db),
                    }
                ]
            )
        if total_pages > 1:
            navigation = []
            if current_page > 0:
                navigation.append({"text": "⬅️ Previous", "callback_data": f"models:providers:{current_page - 1}"})
            if current_page < total_pages - 1:
                navigation.append({"text": "Next ➡️", "callback_data": f"models:providers:{current_page + 1}"})
            rows.append(navigation)
        rows.append(
            [
                {"text": "🩺 Provider health", "callback_data": "provider:health"},
                {"text": "🔄 Refresh models", "callback_data": "provider:refresh"},
            ]
        )
        rows.append(
            [
                {"text": "⬅️ Back to target", "callback_data": "models:target"},
                {"text": "❌ Cancel", "callback_data": "models:cancel"},
            ]
        )
        text = f"Current model: {current_model}\nBridge provider catalog (page {current_page + 1}/{total_pages}):"
        if not options:
            text += (
                "\n\nNo provider models available. Configure models in the private YAML file selected "
                "by SILLYTAVERN_PROVIDER_CONFIG, or enable discover_models and use Refresh models."
            )
    else:
        label, models, is_supported = groups.get(provider_id, (provider_id, [], False))
        page_options, current_page, total_pages = panel_page(models, page)
        rows = []
        for model_label, model_id in page_options:
            mark = "✅ " if model_id == current_model else ""
            callback = (
                "model:" + dynamic_callback_token("model", model_id, chat_id, db=request_context.db)
                if is_supported
                else "unsupported:" + dynamic_callback_token("provider", provider_id, chat_id, db=request_context.db)
            )
            prefix = "" if is_supported else "🚫 "
            rows.append([{"text": prefix + mark + model_label, "callback_data": callback}])
        if total_pages > 1:
            navigation = []
            if current_page > 0:
                navigation.append(
                    {
                        "text": "⬅️ Previous",
                        "callback_data": (
                            "models:model:"
                            f"""{dynamic_callback_token("provider", provider_id, chat_id, db=request_context.db)}"""
                            ":"
                            f"""{current_page - 1}"""
                        ),
                    }
                )
            if current_page < total_pages - 1:
                navigation.append(
                    {
                        "text": "Next ➡️",
                        "callback_data": (
                            "models:model:"
                            f"""{dynamic_callback_token("provider", provider_id, chat_id, db=request_context.db)}"""
                            ":"
                            f"""{current_page + 1}"""
                        ),
                    }
                )
            rows.append(navigation)
        rows.append([{"text": "⬅️ Back to providers", "callback_data": "models:back"}])
        rows.append([{"text": "❌ Cancel", "callback_data": "models:cancel"}])
        text = f"Provider: {label}\nCurrent model: {current_model}"
        if total_pages > 1:
            text += f"\nPage {current_page + 1}/{total_pages}"
        if not is_supported:
            text += "\nCatalog visible; this bridge adapter is not enabled yet."
    try:
        send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id, request_context=request_context)
    except RuntimeError as exc:
        if "not modified" in str(exc).casefold():
            logging.info("Panel already shows the requested state")
            return
        raise


def send_model_target_menu(
    token: str, chat_id: str, current_model: str, utility_model: str, message_id: int | None = None, *, request_context
) -> None:
    markup = {
        "inline_keyboard": [
            [
                {"text": ("✅ " if current_model else "") + "📖 Story model", "callback_data": "modeltarget:story"},
                {"text": ("✅ " if utility_model else "") + "🛠️ Utility model", "callback_data": "modeltarget:utility"},
            ],
            [{"text": "❌ Cancel", "callback_data": "models:cancel"}],
        ]
    }
    send_panel_message(
        token,
        chat_id,
        "Where should the next selected model be used?",
        markup,
        message_id,
        request_context=request_context,
    )


def send_provider_health_menu(token: str, chat_id: str, message_id: int | None = None, *, request_context) -> None:
    checks = provider_health_checks(app_settings=request_context.app_settings)
    lines = [f"{name}: {status}" for _provider_id, name, status in checks]
    text = "Provider health\n\n" + ("\n".join(lines) if lines else "No providers configured.")
    markup = {
        "inline_keyboard": [
            [{"text": "🔄 Refresh models", "callback_data": "provider:refresh"}],
            [
                {"text": "⬅️ Back to providers", "callback_data": "provider:back"},
                {"text": "❌ Close", "callback_data": "models:cancel"},
            ],
        ]
    }
    try:
        send_panel_message(token, chat_id, text, markup, message_id, request_context=request_context)
    except RuntimeError as exc:
        if "not modified" in str(exc).casefold():
            logging.info("Panel already shows the requested state")
            return
        raise
