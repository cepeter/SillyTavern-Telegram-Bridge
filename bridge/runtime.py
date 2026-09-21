"""Plain compatibility facade over ordinary bridge modules.

Production startup is owned by bridge.main. This module only republishes
canonical objects for import compatibility; it performs no source loading and
does not intercept or propagate attribute mutation.
"""
from __future__ import annotations

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

from bridge.group_core import (
    group_state,
    group_user_turn_allowed,
    claim_group_user_turn,
    pass_group_user_turn,
    group_setup_state,
    group_character_option_label,
    save_group_state,
    resolve_character_file,
    group_member_labels,
    group_current_speaker,
    advance_group_turn,
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

from bridge.rag_core import (
    extract_pdf_data_bank_text,
    extract_data_bank_text,
    split_data_bank_chunks,
    rag_embedding_namespace,
    embedding_norm,
    rag_embedding_headers,
    embed_rag_text,
    embed_rag_batch,
    rag_semantic_candidate_limit,
    backfill_rag_embedding_signatures,
    add_data_bank_document,
    cached_rag_embedding,
    retrieve_data_bank,
    rag_mode,
    rag_retrieval_bundle,
    rag_context_for_prompt,
    rag_citation_footer,
    data_bank_documents,
    data_bank_document_versions,
    activate_data_bank_version,
    delete_data_bank_documents,
    rag_embedding_coverage,
    reindex_data_bank_documents,
)

from bridge import (
    callbacks as _callbacks,
    cards as _cards,
    catalog as _catalog,
    character_identity as _character_identity,
    command_routes as _command_routes,
    commands as _commands,
    common as _common,
    director_goals as _director_goals,
    expressions as _expressions,
    generation as _generation,
    greetings as _greetings,
    groups as _groups,
    help as _help,
    help_details as _help_details,
    image_generation as _image_generation,
    input_flows as _input_flows,
    language as _language,
    main as _main,
    media as _media,
    memory as _memory,
    memory_curator as _memory_curator,
    message_commands as _message_commands,
    panel_callback_routes as _panel_callback_routes,
    persona_delete_panel as _persona_delete_panel,
    persona_sync as _persona_sync,
    rag as _rag,
    scene_state as _scene_state,
    session_naming as _session_naming,
    status_panels as _status_panels,
    sync_api as _sync_api,
    sync_core as _sync_core,
    telegram as _telegram,
    update as _update,
)
from bridge.ordinary_dependencies import (
    complete_module_dependencies as _complete_module_dependencies,
    publish_compatibility_namespace as _publish_compatibility_namespace,
)

_APPLICATION_COMPATIBILITY_MODULES = (
    _common,
    _cards,
    _memory,
    _rag,
    _groups,
    _telegram,
    _persona_delete_panel,
    _language,
    _main,
    _greetings,
    _help_details,
    _help,
    _input_flows,
    _catalog,
    _update,
    _image_generation,
    _expressions,
    _media,
    _generation,
    _commands,
    _status_panels,
    _command_routes,
    _message_commands,
    _callbacks,
    _panel_callback_routes,
    _sync_core,
    _sync_api,
    _persona_sync,
    _character_identity,
    _session_naming,
    _scene_state,
    _director_goals,
    _memory_curator,
)

# bridge.main already performs final application composition. Re-completing the
# imported modules here is idempotent and keeps this legacy facade independently
# importable without owning startup.
for _module in _APPLICATION_COMPATIBILITY_MODULES:
    _complete_module_dependencies(_module)

for _module in _APPLICATION_COMPATIBILITY_MODULES:
    _publish_compatibility_namespace(globals(), _module)

del _module
del _APPLICATION_COMPATIBILITY_MODULES
del _complete_module_dependencies
del _publish_compatibility_namespace
