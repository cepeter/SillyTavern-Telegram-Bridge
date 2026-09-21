from __future__ import annotations

from bridge.ordinary_dependencies import bind_module_dependencies as _bind_module_dependencies

def send_persona_delete_menu(token: str, chat_id: str, current_persona: str, message_id: int | None = None, page: int = 0, *, persona_service=None) -> None:
    persona_service = resolve_persona_service(persona_service)
    personas = persona_service.list()
    options = [(persona_id, str(persona.get("name") or persona_id)) for persona_id, persona in personas.items() if persona_id != current_persona]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [[{"text": "🗑️ " + label, "callback_data": "persona:delete:" + dynamic_callback_token("persona", persona_id, chat_id)}] for persona_id, label in page_options]
    navigation = panel_navigation("persona:delete_page", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "⬅️ Back", "callback_data": "persona:menu"}, {"text": "❌ Cancel", "callback_data": "persona:cancel"}])
    payload = {"chat_id": chat_id, "text": "Choose an inactive Persona to delete:", "reply_markup": {"inline_keyboard": rows}}
    send_panel_message(token, chat_id, payload["text"], payload["reply_markup"], message_id)


_bind_module_dependencies(__name__, globals())
