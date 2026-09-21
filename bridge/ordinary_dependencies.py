"""Declared module-local dependencies for the ordinary bridge application.

The legacy runtime used one shared exec namespace, so many modules referenced
collaborators without importing them. Those edges are declared here explicitly.
Bindings are local to each ordinary module; no repository source is executed
into another module's globals.

No declaration may source a dependency from bridge.runtime or bridge.main.
"""
from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import MutableMapping


DependencySpec = tuple[str, str | None]


def _from(module: str, *names: str) -> dict[str, DependencySpec]:
    return {name: (module, name) for name in names}


def _stdlib(*names: str) -> dict[str, DependencySpec]:
    return {name: (name, None) for name in names}


def _merge(*parts: dict[str, DependencySpec]) -> dict[str, DependencySpec]:
    result: dict[str, DependencySpec] = {}
    for part in parts:
        overlap = result.keys() & part.keys()
        if overlap:
            raise RuntimeError(
                "duplicate declared dependency names: "
                + ", ".join(sorted(overlap))
            )
        result.update(part)
    return result


DECLARED_DEPENDENCIES: dict[str, dict[str, DependencySpec]] = {
    "bridge.cards": _merge(
        _from("bridge.persona_sync", "_native_settings", "load_personas", "resolve_persona_service"),
        _from("bridge.telegram", "telegram_request"),
    ),
    "bridge.memory": _merge(
        _from(
            "bridge.config",
            "SUMMARY_MAX_CHARS",
            "SUMMARY_MAX_OUTPUT_TOKENS",
            "SUMMARY_RECENT_MESSAGES",
            "SUMMARY_TRIGGER_MESSAGES",
            "SUMMARY_UPDATE_INTERVAL",
        ),
        _from("bridge.database", "db_connect", "get_generation_settings", "set_meta", "task_model_for_session"),
        _from("bridge.generation", "generate_text"),
        _from("bridge.telegram", "send_text"),
        _from("bridge.common", "submit_background"),
        _stdlib("logging", "sqlite3", "time"),
    ),
    "bridge.rag": _merge(
        _from("bridge.common", "MAX_TELEGRAM_LENGTH"),
        _from("bridge.telegram", "send_text"),
        _from("bridge.database", "set_meta"),
        _stdlib("sqlite3"),
    ),
    "bridge.groups": _merge(
        _from("bridge.config", "DEFAULT_CHARACTER_FILE", "DEFAULT_MODEL", "PENDING_SETTINGS_TTL_SECONDS"),
        _from("bridge.card_content", "card_fields_from_file", "character_card_paths", "safe_character_path"),
        _from("bridge.callbacks", "close_panel_message", "discard_panel_binding"),
        _from("bridge.telegram", "create_session", "load_session", "send_text", "update_session"),
        _from("bridge.callback_tokens", "dynamic_callback_token", "resolve_dynamic_callback_token"),
        _from("bridge.memory", "generate_session_summary"),
        _from("bridge.generation", "generate_text"),
        _from("bridge.database", "get_generation_settings", "set_meta"),
        _from("bridge.panel_utils", "panel_label", "panel_page"),
        _from("bridge.common", "parse_topic_scope"),
        _from("bridge.media", "remove_inline_keyboard", "send_typing"),
        _from("bridge.cards", "send_panel_message"),
        _from("bridge.catalog", "send_world_menu"),
        _from("bridge.session_naming", "start_session_name_input"),
        {"Path": ("pathlib", "Path")},
        _stdlib("json", "logging", "sqlite3", "time"),
    ),
    "bridge.telegram": _merge(
        _from("bridge.config", "CATALOG_MAX_ITEMS", "CHARACTER_DIR", "DEFAULT_CHARACTER_FILE", "RAG_MAX_FILE_BYTES", "RAG_SUPPORTED_SUFFIXES"),
        _from("bridge.common", "CHARACTER_BACKUP_DIR", "DEFAULT_ALLOWED_USER", "IMAGE_MAX_BYTES", "MAX_TELEGRAM_LENGTH", "SYNC_MAX_BYTES", "parse_topic_scope"),
        _from("bridge.persona_sync", "NATIVE_PERSONA_SETTINGS_FILE"),
        _from("bridge.card_content", "active_world_files", "card_fields", "card_fields_from_file", "encode_world_files", "parse_png_chara_bytes", "safe_world_path"),
        _from("bridge.rag_core", "add_data_bank_document", "data_bank_document_versions", "rag_mode"),
        _from("bridge.database", "begin_operation", "bind_panel_session", "db_connect", "get_generation_settings", "get_meta", "optimize_database", "record_operation", "run_write_txn", "set_meta"),
        _from("bridge.runtime_context", "db_connection_context", "panel_actor_context", "panel_session_context"),
        _from("bridge.cards", "default_persona_id", "get_persona", "send_panel_message"),
        _from("bridge.callback_tokens", "dynamic_callback_token"),
        _from("bridge.expressions", "expression_last_key", "expression_mode_key"),
        _from("bridge.memory_backend", "hindsight_session_lock"),
        _from("bridge.catalog", "install_world_info_document"),
        _from("bridge.session_naming", "normalize_session_title"),
        _from("bridge.commands", "process_image_message"),
        _from("bridge.memory", "resolve_memory_service"),
        _from("bridge.generation", "swipe_state_key"),
        _from("bridge.panel_utils", "panel_label", "panel_navigation", "panel_page"),
        {"Path": ("pathlib", "Path")},
        _stdlib("hashlib", "json", "logging", "os", "re", "sqlite3", "time", "urllib"),
    ),
    "bridge.persona_delete_panel": _merge(
        _from("bridge.callback_tokens", "dynamic_callback_token"),
        _from("bridge.panel_utils", "panel_navigation", "panel_page"),
        _from("bridge.persona_sync", "resolve_persona_service"),
        _from("bridge.cards", "send_panel_message"),
    ),
    "bridge.language": _merge(
        _from("bridge.panel_utils", "panel_navigation", "panel_page"),
        _from("bridge.telegram", "send_text", "telegram_request", "update_session"),
        _stdlib("sqlite3"),
    ),
    "bridge.greetings": _merge(
        _from("bridge.config", "CARD_FIELD_MAX_CHARS"),
        _from("bridge.database", "begin_operation", "operation_was_applied", "record_operation"),
        _from("bridge.card_content", "replace_macros"),
        _from("bridge.telegram", "send_text"),
        _stdlib("json", "sqlite3", "time"),
    ),
    "bridge.help_details": _merge(
        _from("bridge.help", "HELP_CATEGORIES", "send_help_menu"),
        _from("bridge.callbacks", "close_panel_message"),
        _from("bridge.panel_utils", "panel_page"),
    ),
    "bridge.help": _merge(
        _from("bridge.config", "GENERATION_DEFAULTS", "PENDING_SETTINGS_TTL_SECONDS", "REASONING_LEVELS"),
        _from("bridge.language", "RESPONSE_LANGUAGES", "normalize_stt_language", "stt_language_label"),
        _from("bridge.common", "STT_DEFAULT_MODEL", "chat_job_lock"),
        {"_jobs_for_services": ("bridge.job_runtime", "jobs_for_services")},
        _from("bridge.rag_core", "activate_data_bank_version", "data_bank_document_versions", "data_bank_documents", "rag_embedding_coverage", "rag_mode", "reindex_data_bank_documents"),
        _from("bridge.commands", "apply_preset_action"),
        _from("bridge.callbacks", "close_panel_message", "discard_panel_binding"),
        _from("bridge.callback_tokens", "dynamic_callback_token", "resolve_dynamic_callback_token"),
        _from("bridge.database", "get_generation_settings", "get_meta", "preset_names", "set_meta", "update_generation_settings"),
        _from("bridge.card_content", "get_system_prompt_choice", "system_prompt_callback_token", "system_prompt_choices"),
        _from("bridge.rag", "handle_data_bank_command"),
        _from("bridge.help_details", "help_markup", "help_text"),
        _from("bridge.telegram", "import_telegram_document", "send_text", "telegram_request"),
        _from("bridge.memory_backend", "memory_mode"),
        _from("bridge.panel_utils", "panel_label", "panel_navigation", "panel_page"),
        _from("bridge.cards", "send_panel_message"),
        _from("bridge.message_commands", "send_reset_confirmation_menu"),
        _from("bridge.runtime_context", "set_db_connection_context"),
        _from("bridge.input_flows", "start_text_action_input"),
        _stdlib("json", "logging", "sqlite3", "time"),
    ),
    "bridge.input_flows": _merge(
        _from("bridge.config", "PENDING_SETTINGS_TTL_SECONDS"),
        _from("bridge.card_content", "card_fields_from_file", "safe_character_path"),
        _from("bridge.callbacks", "close_panel_message", "discard_panel_binding"),
        _from("bridge.common", "delete_pending_input_prompts"),
        _from("bridge.callback_tokens", "dynamic_callback_token", "resolve_dynamic_callback_token"),
        _from("bridge.commands", "edit_last_user", "handle_macro_command", "send_note_menu"),
        _from("bridge.database", "get_generation_settings", "get_meta", "parse_generation_setting", "save_generation_preset", "set_meta", "update_generation_settings"),
        _from("bridge.rag", "handle_data_bank_command"),
        _from("bridge.image_generation", "handle_imagine_prompt"),
        _from("bridge.memory", "handle_memory_command"),
        _from("bridge.session_naming", "handle_session_name_input"),
        _from("bridge.language", "normalize_stt_language", "stt_language_label"),
        _from("bridge.memory_backend", "remember_fact"),
        _from("bridge.media", "remove_inline_keyboard"),
        _from("bridge.persona_sync", "resolve_persona_service"),
        _from("bridge.help", "send_databank_menu", "send_memory_menu", "send_preset_menu", "send_settings_menu", "send_stt_language_menu", "send_voice_input_menu"),
        _from("bridge.status_panels", "send_director_goal_menu"),
        _from("bridge.director_goals", "set_director_goal"),
        _from("bridge.message_commands", "send_pending_input_message"),
        _from("bridge.persona_delete_panel", "send_persona_delete_menu"),
        _from("bridge.cards", "send_panel_message", "send_persona_menu"),
        _from("bridge.telegram", "send_text", "telegram_request", "update_session"),
        _stdlib("html", "json", "logging", "re", "sqlite3", "threading", "time"),
    ),
    "bridge.catalog": _merge(
        _from("bridge.common", "MODEL_CACHE_FILE", "MODEL_CHOICES", "MODEL_REFRESH_SECONDS", "PROVIDER_CONFIG_FILE"),
        _from("bridge.config", "WORLD_DIR"),
        _from("bridge.card_content", "active_world_files", "safe_world_path", "world_file_paths"),
        _from("bridge.callback_tokens", "dynamic_callback_token"),
        _from("bridge.generation", "opencode_muse_headers"),
        _from("bridge.panel_utils", "panel_label", "panel_navigation", "panel_page"),
        _from("bridge.cards", "send_panel_message"),
        _from("bridge.network_security", "strict_urlopen", "validate_provider_endpoint"),
        _from("bridge.telegram", "telegram_request"),
        {"Path": ("pathlib", "Path")},
        _stdlib("json", "logging", "os", "tempfile", "time", "urllib"),
    ),
    "bridge.update": _merge(
        _from("bridge.catalog", "answer_callback"),
        _from("bridge.media", "remove_inline_keyboard"),
        _from("bridge.telegram", "send_text", "telegram_request"),
    ),
    "bridge.image_generation": _merge(
        _from("bridge.common", "IMAGE_MAX_BYTES", "PROVIDER_CONFIG_FILE", "parse_topic_scope"),
        _from("bridge.network_security", "strict_urlopen", "validate_provider_endpoint"),
        _from("bridge.telegram", "send_text"),
        _stdlib("base64", "json", "os", "re", "time", "urllib"),
    ),
    "bridge.expressions": _merge(
        _from("bridge.config", "CARD_FILE", "DEFAULT_CHARACTER_FILE", "SILLYTAVERN_DIR"),
        _from("bridge.common", "IMAGE_MAX_BYTES", "parse_topic_scope"),
        _from("bridge.database", "get_meta", "set_meta"),
        _from("bridge.panel_utils", "panel_navigation", "panel_page"),
        _from("bridge.card_content", "safe_character_path"),
        _from("bridge.telegram", "telegram_request"),
        {"Path": ("pathlib", "Path")},
        _stdlib("json", "logging", "sqlite3", "time", "urllib"),
    ),
    "bridge.media": _merge(
        _from("bridge.config", "BRIDGE_HOME"),
        _from("bridge.common", "PROVIDER_CONFIG_FILE", "STT_DEFAULT_MODEL", "STT_MAX_BYTES", "TTS_MAX_CHARS", "chat_job_lock", "parse_topic_scope", "submit_background"),
        {"_jobs_for_services": ("bridge.job_runtime", "jobs_for_services")},
        _from("bridge.database", "begin_operation", "clear_failed_turn", "committed_assistant_for_message", "db_connect", "get_meta", "operation_was_applied", "record_operation", "run_write_txn"),
        _from("bridge.callbacks", "close_panel_message"),
        _from("bridge.expressions", "deliver_expression"),
        _from("bridge.telegram", "download_telegram_file", "ensure_session", "send_text", "telegram_request"),
        _from("bridge.message_commands", "process_message"),
        _from("bridge.runtime_context", "set_db_connection_context", "set_panel_actor_context"),
        _stdlib("hashlib", "json", "logging", "re", "sqlite3", "tempfile", "threading", "time", "urllib"),
    ),
    "bridge.generation": _merge(
        _from("bridge.config", "DEFAULT_MAX_TOKENS", "DEFAULT_USER_NAME", "GENERATION_DEFAULTS", "HINDSIGHT_CONTEXT_MAX_CHARS", "RAG_MAX_CONTEXT_CHARS", "SUMMARY_MAX_CHARS"),
        _from("bridge.common", "DEFAULT_PROVIDER_URL", "PROVIDER_CONFIG_FILE"),
        _from("bridge.card_content", "active_world_files", "build_system_prompt", "build_world_info", "replace_macros"),
        _from("bridge.database", "begin_operation", "get_generation_settings", "get_meta", "operation_phase", "record_operation", "run_write_txn", "set_meta", "set_operation_phase"),
        _from("bridge.media", "delete_outgoing_message_row", "get_provider_spec", "send_reply", "send_typing"),
        _from("bridge.language", "normalize_response_language", "response_language_instruction", "response_language_label"),
        _from("bridge.rag_core", "rag_citation_footer", "rag_context_for_prompt", "rag_retrieval_bundle"),
        _from("bridge.memory", "resolve_memory_service"),
        _from("bridge.persona_sync", "resolve_persona_service"),
        _from("bridge.telegram", "send_text", "telegram_request"),
        {"Path": ("pathlib", "Path")},
        _stdlib("json", "logging", "os", "sqlite3", "urllib"),
    ),
    "bridge.commands": _merge(
        _from("bridge.common", "MAX_HISTORY_MESSAGES"),
        _from("bridge.group_core", "advance_group_turn", "group_current_speaker", "group_state"),
        _from("bridge.generation", "build_chat_messages", "generate_text", "render_session_response", "save_response_variant"),
        _from("bridge.card_content", "card_fields_from_file", "replace_macros"),
        _from("bridge.context_compaction", "context_history_candidate_limit", "context_input_budget_tokens"),
        _from("bridge.rag_core", "data_bank_documents", "rag_citation_footer", "rag_context_for_prompt", "rag_mode", "rag_retrieval_bundle"),
        _from("bridge.database", "delete_generation_preset", "format_generation_settings", "get_generation_settings", "get_meta", "load_generation_preset", "begin_operation", "operation_phase", "record_operation", "run_write_txn", "set_operation_phase", "update_generation_settings", "write_transaction"),
        _from("bridge.media", "delete_outgoing_message_row", "send_reply", "send_typing"),
        _from("bridge.telegram", "ensure_session", "send_text", "telegram_request"),
        _from("bridge.groups", "group_prompt_context"),
        _from("bridge.memory_backend", "memory_mode", "memory_scope"),
        _from("bridge.cards", "persona_name"),
        _from("bridge.memory", "resolve_memory_service"),
        _from("bridge.message_commands", "send_reset_confirmation_menu"),
        _stdlib("base64", "logging", "re", "sqlite3", "time"),
    ),
    "bridge.status_panels": _merge(
        _from("bridge.config", "DEFAULT_MODEL"),
        _from("bridge.card_content", "active_world_files", "card_fields_from_file", "system_prompt_label"),
        _from("bridge.scene_state", "clear_scene_state", "get_scene_state", "refresh_scene_state_now"),
        _from("bridge.callbacks", "close_panel_message"),
        _from("bridge.context_compaction", "context_history_candidate_limit", "context_input_budget_tokens"),
        _from("bridge.memory_curator", "curate_memory_now", "curated_memory_text"),
        _from("bridge.rag_core", "data_bank_documents", "rag_mode"),
        _from("bridge.expressions", "expression_mode_key"),
        _from("bridge.director_goals", "get_director_goal", "set_director_goal"),
        _from("bridge.database", "get_generation_settings", "get_meta", "task_model_for_session"),
        _from("bridge.memory", "get_session_summary"),
        _from("bridge.group_core", "group_member_labels", "group_state"),
        _from("bridge.groups", "handle_summary_command"),
        _from("bridge.memory_backend", "memory_mode", "memory_scope"),
        _from("bridge.cards", "persona_name", "send_panel_message"),
        _from("bridge.commands", "prompt_diagnostics"),
        _from("bridge.sync_api", "resolve_sync_service"),
        _from("bridge.language", "response_language_label"),
        _from("bridge.help", "send_memory_menu"),
        _from("bridge.telegram", "send_text"),
        _from("bridge.media", "send_typing"),
        _from("bridge.input_flows", "start_text_action_input"),
        {"Path": ("pathlib", "Path")},
        _stdlib("time"),
    ),
    "bridge.command_routes": _merge(
        _from("bridge.card_content", "active_world_files"),
        _from("bridge.database", "clear_failed_turn", "committed_assistant_for_message", "latest_failed_turn", "record_failed_turn", "task_model_for_session"),
        _from("bridge.generation", "continue_last", "regenerate_last", "send_swipe_menu"),
        _from("bridge.rag", "handle_data_bank_command"),
        _from("bridge.groups", "handle_group_command", "send_group_menu"),
        _from("bridge.image_generation", "handle_imagine_prompt"),
        _from("bridge.input_flows", "handle_inline_text_action", "start_text_action_input"),
        _from("bridge.language", "handle_language_command", "send_language_menu"),
        _from("bridge.memory", "handle_memory_command"),
        _from("bridge.telegram", "list_sessions", "send_text"),
        _from("bridge.common", "parse_topic_scope"),
        _from("bridge.message_commands", "process_message"),
        _from("bridge.commands", "prompt_diagnostics", "send_note_menu", "send_stscript_menu"),
        _from("bridge.greetings", "send_character_greeting"),
        _from("bridge.cards", "send_character_menu", "send_persona_menu", "send_session_menu"),
        _from("bridge.help", "send_databank_menu", "send_help_menu", "send_memory_menu", "send_preset_menu", "send_settings_menu", "send_stream_menu", "send_stt_language_menu", "send_system_prompt_menu", "send_voice_input_menu", "send_voice_menu"),
        _from("bridge.help_details", "send_help_command"),
        _from("bridge.expressions", "send_expression_menu"),
        _from("bridge.catalog", "send_model_target_menu", "send_world_menu"),
        _from("bridge.status_panels", "send_prompt_menu", "send_summary_menu", "send_sync_menu", "status_text"),
        _from("bridge.media", "send_reply"),
        _from("bridge.update", "send_update_menu"),
        _from("bridge.session_naming", "start_session_name_input"),
        _stdlib("logging"),
    ),
    "bridge.message_commands": _merge(
        _from("bridge.config", "DEFAULT_USER_NAME"),
        _from("bridge.group_core", "advance_group_turn", "group_current_speaker"),
        _from("bridge.database", "begin_operation", "clear_failed_turn", "get_generation_settings", "get_meta", "operation_phase", "operation_was_applied", "optimize_database", "record_operation", "run_write_txn", "set_meta", "set_operation_phase", "write_transaction"),
        _from("bridge.generation", "build_chat_messages", "continue_last", "generate_text", "regenerate_last", "render_response_language", "save_response_variant", "swipe_state_key"),
        _from("bridge.card_content", "card_fields_from_file"),
        _from("bridge.memory", "clear_session_summary", "resolve_memory_service"),
        _from("bridge.context_compaction", "context_history_candidate_limit"),
        _from("bridge.commands", "edit_last_user"),
        _from("bridge.telegram", "ensure_session", "list_sessions", "load_session", "send_text", "telegram_request"),
        _from("bridge.groups", "group_director_plan", "group_prompt_context"),
        _from("bridge.command_routes", "handle_command_route"),
        _from("bridge.input_flows", "handle_pending_input"),
        _from("bridge.language", "normalize_response_language"),
        _from("bridge.cards", "persona_name", "send_session_menu"),
        _from("bridge.media", "queue_user_quote_tts", "send_reply", "send_typing"),
        _from("bridge.rag_core", "rag_citation_footer", "rag_context_for_prompt", "rag_retrieval_bundle"),
        _from("bridge.character_identity", "reconcile_session_character"),
        _from("bridge.runtime_context", "set_panel_session_context"),
        _from("bridge.performance", "timed_call"),
        _stdlib("json", "logging", "sqlite3", "time"),
    ),
    "bridge.callbacks": _merge(
        _from("bridge.config", "DEFAULT_MODEL"),
        _from("bridge.database", "db_connect", "panel_owner_for_message", "panel_session_for_message"),
        _from("bridge.runtime_context", "db_connection_context", "set_panel_session_context"),
        _from("bridge.telegram", "ensure_session", "load_session", "send_text", "telegram_request"),
        _from("bridge.panel_callback_routes", "handle_entity_panel_callback", "handle_primary_panel_callback", "handle_provider_model_callback"),
        _from("bridge.help", "handle_enum_callback"),
        _from("bridge.groups", "handle_group_panel_callback"),
        _from("bridge.catalog", "answer_callback"),
        _from("bridge.common", "parse_topic_scope"),
        _from("bridge.media", "remove_inline_keyboard"),
        _from("bridge.callback_tokens", "resolve_dynamic_callback_token"),
        _stdlib("logging", "sqlite3"),
    ),
    "bridge.panel_callback_routes": _merge(
        _from("bridge.config", "CARD_FILE", "DEFAULT_CHARACTER_FILE", "DEFAULT_MODEL", "PENDING_SETTINGS_TTL_SECONDS"),
        _from("bridge.card_content", "active_world_files", "card_fields_from_file", "encode_world_files", "get_system_prompt_choice", "safe_character_path", "safe_world_path"),
        _from("bridge.groups", "apply_group_setup_character", "send_group_menu"),
        _from("bridge.group_core", "group_setup_state"),
        _from("bridge.database", "begin_operation", "clear_model_target_selection", "get_meta", "get_model_target_selection", "record_operation", "set_meta", "set_model_target_selection", "set_task_model", "task_model_for_session"),
        _from("bridge.telegram", "character_delete_references", "delete_session_data", "list_sessions", "send_session_delete_confirm", "send_session_delete_menu", "send_text", "telegram_request", "update_session", "verify_character_card_backup"),
        _from("bridge.callbacks", "close_panel_message", "discard_panel_binding"),
        _from("bridge.media", "delete_outgoing_messages", "remove_inline_keyboard"),
        _from("bridge.catalog", "delete_world_info_file", "refresh_model_catalog", "send_model_menu", "send_model_target_menu", "send_provider_health_menu", "send_world_menu"),
        _from("bridge.expressions", "discover_expression_assets", "expression_last_key", "expression_mode_key", "send_expression_menu"),
        _from("bridge.callback_tokens", "dynamic_callback_token", "resolve_dynamic_callback_token"),
        _from("bridge.generation", "edit_swipe_menu", "keep_swipe_variant", "last_user_variants", "swipe_state_key"),
        _from("bridge.help_details", "handle_help_callback"),
        _from("bridge.input_flows", "handle_persona_callback", "pending_character_for_session"),
        _from("bridge.status_panels", "handle_prompt_and_feature_callback", "send_sync_menu"),
        _from("bridge.update", "handle_update_callback"),
        _from("bridge.message_commands", "reset_session"),
        _from("bridge.commands", "send_note_menu"),
        _from("bridge.help", "send_system_prompt_menu"),
        _from("bridge.sync_api", "resolve_sync_service"),
        _from("bridge.language", "response_language_label", "send_language_menu", "set_response_language"),
        _from("bridge.cards", "send_character_delete_confirm", "send_character_delete_menu", "send_character_info_menu", "send_character_menu", "send_panel_message", "send_session_menu"),
        _from("bridge.session_naming", "start_session_name_input"),
        {"Path": ("pathlib", "Path")},
        _stdlib("json", "logging", "time"),
    ),
    "bridge.sync_core": _merge(
        _from("bridge.config", "DEFAULT_MODEL", "DEFAULT_USER_NAME", "GENERATION_DEFAULTS"),
        _from("bridge.card_content", "active_world_files", "card_fields_from_file", "encode_world_files", "safe_character_path", "safe_world_path"),
        _from("bridge.database", "ensure_sync_binding", "get_generation_settings", "parse_generation_setting", "sync_transcript_hash", "update_generation_settings"),
        _from("bridge.cards", "get_persona", "persona_name"),
        _from("bridge.memory", "get_session_summary", "retain_session_memory"),
        _from("bridge.telegram", "load_session", "update_session"),
        _from("bridge.language", "normalize_response_language"),
        _from("bridge.generation", "save_response_variant"),
        _stdlib("logging"),
    ),
    "bridge.sync_api": _merge(
        _from("bridge.config", "DEFAULT_MODEL"),
        _from("bridge.sync_core", "SYNC_MAX_PAYLOAD_BYTES", "apply_sync_snapshot", "build_sync_records", "set_sync_state", "sync_binding", "sync_file_id", "sync_local_rows"),
        _from("bridge.card_content", "card_fields_from_file"),
        _from("bridge.common", "chat_job_lock"),
        _from("bridge.database", "db_connect", "run_write_txn", "sync_transcript_hash"),
        _from("bridge.group_core", "group_state"),
        _from("bridge.telegram", "load_session"),
    ),
    "bridge.persona_sync": _merge(
        _from("bridge.config", "CATALOG_MAX_ITEMS"),
        _from("bridge.common", "IMAGE_MAX_BYTES"),
        _from("bridge.input_flows", "PERSONA_EDIT_LOCK"),
        _from("bridge.sync_core", "SYNC_MAX_PAYLOAD_BYTES"),
        _from("bridge.sync_api", "SillyTavernApiError", "phase3_api_configured", "phase3_client"),
        _from("bridge.cards", "default_persona_id"),
        _from("bridge.telegram", "update_session"),
    ),
    "bridge.character_identity": _merge(
        _from("bridge.common", "CHARACTER_BACKUP_DIR", "IMAGE_MAX_BYTES"),
        _from("bridge.card_content", "character_card_paths", "safe_character_path"),
        _from("bridge.telegram", "update_session"),
    ),
    "bridge.session_naming": _merge(
        _from("bridge.config", "DEFAULT_MODEL", "PENDING_SETTINGS_TTL_SECONDS"),
        _from("bridge.input_flows", "_cancel_pending", "pending_character_for_session"),
        _from("bridge.callbacks", "close_panel_message", "discard_panel_binding"),
        _from("bridge.telegram", "create_session", "send_text", "update_session"),
        _from("bridge.common", "delete_pending_input_prompts"),
        _from("bridge.database", "get_meta", "set_meta"),
        _from("bridge.cards", "send_character_menu"),
        _from("bridge.message_commands", "send_pending_input_message"),
        _from("bridge.runtime_context", "set_panel_session_context"),
        _from("bridge.groups", "start_group_session"),
    ),
    "bridge.scene_state": _merge(
        _from("bridge.config", "DEFAULT_MODEL"),
        _from("bridge.database", "db_connect", "get_generation_settings", "task_model_for_session", "write_transaction"),
        _from("bridge.generation", "generate_text"),
        _from("bridge.telegram", "load_session", "send_text"),
        _from("bridge.status_panels", "send_scene_menu"),
        _from("bridge.media", "send_typing"),
        _from("bridge.common", "submit_background"),
        _stdlib("sqlite3"),
    ),
    "bridge.director_goals": _merge(
        _from("bridge.common", "parse_topic_scope"),
        _from("bridge.status_panels", "send_director_goal_menu"),
        _from("bridge.telegram", "send_text"),
        _from("bridge.database", "task_model_for_session", "write_transaction"),
        _stdlib("sqlite3"),
    ),
    "bridge.memory_curator": _merge(
        _from("bridge.config", "DEFAULT_MODEL"),
        _from("bridge.memory_backend", "_retain_with_client", "hindsight_session_prefix", "memory_mode"),
        _from("bridge.database", "db_connect", "get_generation_settings", "task_model_for_session", "write_transaction"),
        _from("bridge.generation", "generate_text"),
        _from("bridge.telegram", "load_session", "send_text"),
        _from("bridge.status_panels", "send_curated_memory_menu"),
        _from("bridge.media", "send_typing"),
        _from("bridge.common", "submit_background"),
        _stdlib("sqlite3"),
    ),
    "bridge.main": _merge(
        _from("bridge.config", "CARD_FILE", "CHARACTER_DIR", "DB_FILE", "DEFAULT_CHARACTER_FILE"),
        _from("bridge.input_flows", "PERSONA_EDIT_LOCK"),
        _from(
            "bridge.sync_api",
            "SillyTavernApiError",
            "_phase3_disable",
            "phase3_api_configured",
            "phase3_client",
            "phase3_sync_now",
            "phase3_sync_poll",
            "phase3_toggle_realtime",
            "refresh_phase3_config",
            "start_phase3_sync_worker",
            "stop_phase3_sync_worker",
        ),
        _from(
            "bridge.group_core",
            "advance_group_turn",
            "group_current_speaker",
            "group_member_labels",
            "group_state",
            "group_user_turn_allowed",
        ),
        _from("bridge.catalog", "answer_callback"),
        _from(
            "bridge.common",
            "begin_background_shutdown",
            "chat_job_lock",
            "enforce_runtime_permissions",
            "load_env_file",
            "register_durable_backlog_dispatcher",
            "shutdown_background_executors",
            "submit_chat_background",
            "topic_scope_from_message",
        ),
        _from(
            "bridge.card_content",
            "card_fields",
            "card_fields_from_file",
            "read_png_chara",
            "safe_character_path",
        ),
        _from(
            "bridge.database",
            "clear_failed_turn",
            "committed_assistant_for_message",
            "db_connect",
            "enqueue_job",
            "finish_job",
            "get_generation_settings",
            "get_meta",
            "job_actor_id",
            "mark_job_running",
            "mark_job_scheduled",
            "operation_phase",
            "operation_was_applied",
            "record_failed_turn",
            "record_operation",
            "recover_jobs",
            "run_database_maintenance",
            "run_write_txn",
            "set_meta",
        ),
        _from("bridge.cards", "default_persona_id"),
        _from("bridge.persona_sync", "delete_native_persona", "load_personas", "upsert_native_persona"),
        _from("bridge.commands", "edit_telegram_user_message"),
        _from(
            "bridge.telegram",
            "ensure_session",
            "load_session",
            "process_telegram_image",
            "send_text",
            "telegram_request",
            "update_session",
        ),
        _from("bridge.generation", "generate_text", "resolve_provider_model"),
        _from("bridge.media", "get_provider_spec", "process_voice_job", "send_reply"),
        _from(
            "bridge.memory",
            "get_session_summary",
            "purge_hindsight_session",
            "retain_session_memory",
            "session_summary_for_prompt",
        ),
        _from("bridge.help_details", "handle_help_callback", "is_help_callback", "send_help_command"),
        _from("bridge.callbacks", "process_callback"),
        _from("bridge.message_commands", "process_message"),
        _from("bridge.help", "process_document_job", "set_bot_commands"),
        _from("bridge.memory_backend", "recall_memory_context"),
        _from("bridge.runtime_context", "set_db_connection_context", "set_panel_actor_context"),
        _from("bridge.sync_core", "sync_binding"),
        {"Path": ("pathlib", "Path")},
        _stdlib("argparse", "json", "logging", "os", "signal", "sqlite3", "threading", "time", "urllib"),
    ),
}


def declared_dependencies_for(module_name: str) -> dict[str, DependencySpec]:
    return dict(DECLARED_DEPENDENCIES.get(module_name, {}))


_MISSING = object()


def _resolve_dependency(spec: DependencySpec, *, defer_cycles: bool = False):
    module_name, attribute = spec
    if module_name == "urllib" and attribute is None:
        importlib.import_module("urllib.error")
        importlib.import_module("urllib.parse")
        importlib.import_module("urllib.request")

    if defer_cycles and module_name in DECLARED_DEPENDENCIES:
        module = sys.modules.get(module_name)
        if module is None:
            return _MISSING
        if attribute is not None and not hasattr(module, attribute):
            return _MISSING
    else:
        module = importlib.import_module(module_name)

    return module if attribute is None else getattr(module, attribute)


def bind_module_dependencies(
    module_name: str,
    namespace: MutableMapping[str, object],
) -> None:
    """Bind safe dependencies during one module's ordinary import.

    Dependencies on another declared application module are deferred when that
    peer is not fully initialized yet. The composition pass completes those
    edges after all ordinary modules have imported.
    """
    declarations = DECLARED_DEPENDENCIES.get(module_name, {})
    for name, spec in declarations.items():
        if name in namespace:
            continue
        source_module = spec[0]
        if source_module in {"bridge.runtime", "bridge.main"}:
            raise RuntimeError(
                f"{module_name} declares forbidden dependency {name} from {source_module}"
            )
        value = _resolve_dependency(spec, defer_cycles=True)
        if value is not _MISSING:
            namespace[name] = value


def complete_module_dependencies(module: ModuleType) -> None:
    """Complete every declared dependency after the import graph is loaded."""
    declarations = DECLARED_DEPENDENCIES.get(module.__name__, {})
    for name, spec in declarations.items():
        source_module = spec[0]
        if source_module in {"bridge.runtime", "bridge.main"}:
            raise RuntimeError(
                f"{module.__name__} declares forbidden dependency {name} from {source_module}"
            )
        setattr(module, name, _resolve_dependency(spec))


def complete_application_dependencies(
    module_name: str,
    namespace: MutableMapping[str, object],
) -> tuple[ModuleType, ...]:
    """Import the declared application graph and complete module-local edges."""
    modules: list[ModuleType] = []
    for declared_module_name in DECLARED_DEPENDENCIES:
        if declared_module_name == module_name:
            continue
        module = importlib.import_module(declared_module_name)
        modules.append(module)

    for module in modules:
        complete_module_dependencies(module)

    declarations = DECLARED_DEPENDENCIES.get(module_name, {})
    for name, spec in declarations.items():
        source_module = spec[0]
        if source_module in {"bridge.runtime", "bridge.main"}:
            raise RuntimeError(
                f"{module_name} declares forbidden dependency {name} from {source_module}"
            )
        namespace[name] = _resolve_dependency(spec)

    return tuple(modules)


def publish_compatibility_namespace(
    target: MutableMapping[str, object],
    module: ModuleType,
) -> None:
    """Publish an ordinary module namespace into a plain compatibility facade."""
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        target[name] = value
