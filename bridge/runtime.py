"""Compatibility facade for the staged bridge runtime.

Domain files still share one namespace so existing handlers keep their call-time
late binding semantics. The loader now validates load phases and makes
intentional recovery/safety overrides explicit and auditable.
"""
from __future__ import annotations

from pathlib import Path as _RuntimePath

from bridge.database import (
    begin_operation,
    bind_panel_session,
    clear_failed_turn,
    clear_model_target_selection,
    committed_assistant_for_message,
    db_connect,
    delete_generation_preset,
    enqueue_job,
    ensure_sync_binding,
    finish_job,
    format_generation_settings,
    get_generation_settings,
    get_meta,
    get_model_target_selection,
    job_actor_id,
    latest_failed_turn,
    load_generation_preset,
    mark_job_running,
    mark_job_scheduled,
    model_target_selection_key,
    operation_phase,
    operation_was_applied,
    optimize_database,
    panel_owner_for_message,
    panel_session_for_message,
    parse_generation_setting,
    preset_names,
    record_failed_turn,
    record_operation,
    recover_jobs,
    run_database_maintenance,
    run_write_txn,
    save_generation_preset,
    set_meta,
    set_model_target_selection,
    set_operation_phase,
    set_task_model,
    sync_transcript_hash,
    task_model_for_session,
    task_model_key,
    update_generation_settings,
    write_transaction,
)

from bridge.native_cache import (
    cached_json,
    cached_png_metadata,
    cached_text,
)
from bridge.performance import (
    perf_span,
    performance_enabled,
    timed_call,
)
from bridge.schema import (
    SCHEMA_MIGRATIONS,
    initialize_database_schema,
)

from bridge.runtime_context import (
    db_connection_context,
    panel_actor_context,
    panel_session_context,
    set_db_connection_context,
    set_panel_actor_context,
    set_panel_session_context,
)

from bridge.callback_tokens import (
    dynamic_callback_token,
    resolve_dynamic_callback_token,
)

from bridge.card_content import (
    active_world_files,
    build_system_prompt,
    build_world_info,
    card_fields,
    card_fields_from_file,
    character_card_paths,
    character_display_name,
    encode_world_files,
    get_system_prompt_choice,
    load_system_prompts,
    parse_png_chara_bytes,
    read_png_chara,
    replace_macros,
    safe_character_path,
    safe_world_path,
    system_prompt_callback_token,
    system_prompt_choices,
    system_prompt_label,
    world_file_paths,
)
from bridge.panel_utils import (
    panel_label,
    panel_navigation,
    panel_page,
)

from bridge.memory_backend import (
    close_hindsight_client,
    hindsight_bank_id,
    hindsight_client,
    hindsight_conversation_document_id,
    hindsight_explicit_document_id,
    hindsight_session_lock,
    hindsight_session_prefix,
    hindsight_tags,
    memory_mode,
    memory_recall_filter,
    memory_scope,
    recall_memory_context,
    recall_memory_results,
    remember_fact,
)

from bridge.network_security import (
    strict_urlopen,
    validate_provider_endpoint,
)

from bridge.runtime_loader import (
    DEFAULT_RUNTIME_STAGES as _DEFAULT_RUNTIME_STAGES,
    load_runtime_namespace as _load_runtime_namespace,
)


RUNTIME_LOAD_REPORT = _load_runtime_namespace(
    globals(),
    _RuntimePath(__file__).parent,
    _DEFAULT_RUNTIME_STAGES,
)

del _RuntimePath, _DEFAULT_RUNTIME_STAGES, _load_runtime_namespace
