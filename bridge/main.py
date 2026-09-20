from functools import partial as _partial

from bridge.composition import (
    BackgroundRuntime as _BackgroundRuntime,
    BridgeConfig as _BridgeConfig,
    BridgeServices as _BridgeServices,
    TelegramRuntime as _TelegramRuntime,
    build_bridge_services as _build_bridge_services_value,
    load_bridge_config as _load_bridge_config_value,
    validate_bridge_config as _validate_bridge_config_value,
)
from bridge.extension_registry import (
    get_director_customization as _get_director_customization_value,
)
from bridge.group_director_service import (
    GroupDirectorService as _GroupDirectorService,
)
from bridge.job_service import (
    JobService as _JobService,
    JobSubmission as _JobSubmission,
)
from bridge.memory_service import (
    MemoryService as _MemoryService,
)
from bridge.persona_service import (
    PersonaService as _PersonaService,
)
from bridge.sync_service import (
    SyncService as _SyncService,
)
from bridge.repositories import (
    count_persona_references as _count_persona_references,
    count_session_messages as _count_session_messages,
)

_SHUTDOWN_EVENT = threading.Event()


def request_bridge_shutdown(
    on_shutdown,
    signum=None,
    _frame=None,
) -> None:
    if signum is not None:
        logging.info("Bridge shutdown requested by signal %s", signum)
    _SHUTDOWN_EVENT.set()
    on_shutdown()


def install_bridge_signal_handlers(on_shutdown) -> None:
    def handle(signum, frame):
        request_bridge_shutdown(on_shutdown, signum, frame)

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(signum, handle)
        except (ValueError, OSError):
            logging.debug("Could not install signal handler %s", signum, exc_info=True)


LONG_RUNNING_COMMANDS = (
    "/retry", "/regen", "/continue", "/edit", "/summarize",
    "/providers health", "/providers refresh",
)


def is_long_running_command(text: str) -> bool:
    normalized = str(text).strip().casefold()
    return any(normalized == name or normalized.startswith(name + " ") for name in LONG_RUNNING_COMMANDS)


def validate_startup_credential(model: str) -> None:
    provider_id, _ = resolve_provider_model(model)
    spec = get_provider_spec(provider_id)
    transport = str(spec.get("transport") or "")
    if transport == "opencode_muse":
        return
    configured_key_env = spec.get("api_key_env")
    if configured_key_env:
        key_envs = [str(configured_key_env)]
    elif transport == "anthropic_messages":
        key_envs = ["ANTHROPIC_API_KEY", "LLM_API_KEY"]
    else:
        key_envs = ["LLM_API_KEY"]
    if not any(os.environ.get(key_env) for key_env in key_envs):
        raise RuntimeError(f"required provider credential is missing; set one of: {', '.join(key_envs)}")


def process_message_job(
    services: _BridgeServices,
    fields: dict,
    chat_id: str,
    text: str,
    message_id: int,
    queued_session_id: str | None = None,
    model_override: str | None = None,
    job_id: int | None = None,
) -> None:
    token = services.config.bot_token
    api_key = services.config.api_key
    model = model_override or services.config.default_model
    jobs = _jobs_for_services(services)
    with chat_job_lock(chat_id):
        db = services.db_factory()
        set_db_connection_context(db)
        try:
            if job_id is not None and not jobs.start(db, job_id):
                return
            set_panel_actor_context(jobs.actor_id(db, job_id))
            existing = committed_assistant_for_message(db, chat_id, message_id)
            if existing:
                recovery_session = load_session(db, chat_id, queued_session_id, model) if queued_session_id else ensure_session(db, chat_id, model)
                if group_current_speaker(db, chat_id, recovery_session, text):
                    advance_group_turn(db, chat_id, recovery_session["session_id"], operation_id=job_id)
                if json.loads(existing[2] or "[]"):
                    clear_failed_turn(db, chat_id, message_id)
                    if job_id is not None:
                        jobs.complete(db, job_id)
                    return
                delivery_session_id = queued_session_id or ensure_session(db, chat_id, model)["session_id"]
                send_reply(token, chat_id, str(existing[1]), db, delivery_session_id, int(existing[0]))
                clear_failed_turn(db, chat_id, message_id)
                if job_id is not None:
                    jobs.complete(db, job_id)
                return
            process_message(
                db,
                token,
                api_key,
                model,
                fields,
                chat_id,
                text,
                message_id,
                queued_session_id=queued_session_id,
                operation_id=job_id,
                services=services,
            )
            if job_id is not None:
                jobs.complete(db, job_id)
        except Exception as exc:
            logging.error("Background message processing failed: %s", exc, exc_info=True)
            record_failed_turn(db, chat_id, message_id, text, model, str(exc), queued_session_id or "")
            if job_id is not None:
                jobs.fail(db, job_id, exc)
            if str(text).lstrip().startswith("/"):
                if "Telegram sendMessage failed" in str(exc):
                    failure_message = "Telegram could not deliver this command. The character backend was not called; retry the command."
                else:
                    failure_message = "The command failed. Use /status for details, then retry the command."
            else:
                failure_message = "The character backend failed for this message. Use /retry or /status."
            services.telegram.send_text(token, chat_id, failure_message)
        finally:
            set_panel_actor_context(None)
            set_db_connection_context(None)
            db.close()


def process_image_job(
    services: _BridgeServices,
    chat_id: str,
    file_id: str,
    caption: str,
    file_size: int,
    message_id: int,
    queued_session_id: str | None = None,
    model_override: str | None = None,
    job_id: int | None = None,
) -> None:
    token = services.config.bot_token
    model = model_override or services.config.default_model
    jobs = _jobs_for_services(services)
    with chat_job_lock(chat_id):
        db = services.db_factory()
        set_db_connection_context(db)
        try:
            if job_id is not None and not jobs.start(db, job_id):
                return
            existing = committed_assistant_for_message(db, chat_id, message_id)
            if existing:
                if json.loads(existing[2] or "[]"):
                    clear_failed_turn(db, chat_id, message_id)
                    if job_id is not None:
                        jobs.complete(db, job_id)
                    return
                delivery_session_id = queued_session_id or ensure_session(db, chat_id, model)["session_id"]
                send_reply(token, chat_id, str(existing[1]), db, delivery_session_id, int(existing[0]))
                clear_failed_turn(db, chat_id, message_id)
                if job_id is not None:
                    jobs.complete(db, job_id)
                return
            process_telegram_image(
                db,
                token,
                chat_id,
                file_id,
                caption,
                model,
                file_size,
                message_id,
                queued_session_id=queued_session_id,
                memory_service=services.memory,
                persona_service=services.persona,
            )
            if job_id is not None:
                jobs.complete(db, job_id)
        except Exception as exc:
            logging.error("Background image processing failed: %s", exc, exc_info=True)
            if job_id is not None:
                jobs.fail(db, job_id, exc)
            services.telegram.send_text(token, chat_id, "Image processing failed. The selected model may not support vision.")
        finally:
            set_db_connection_context(None)
            db.close()


def process_callback_job(
    services: _BridgeServices,
    chat_id: str,
    callback: dict,
    job_id: int | None = None,
) -> None:
    token = services.config.bot_token
    jobs = _jobs_for_services(services)
    with chat_job_lock(chat_id):
        db = services.db_factory()
        set_db_connection_context(db)
        try:
            if job_id is not None and not jobs.start(db, job_id):
                return
            actor_id = (
                jobs.actor_id(db, job_id)
                if job_id is not None
                else ""
            )
            set_panel_actor_context(
                actor_id
                or str((callback.get("from") or {}).get("id", ""))
            )
            if job_id is not None and operation_was_applied(db, job_id):
                jobs.complete(db, job_id)
                return
            process_callback(
                db,
                token,
                callback,
                operation_id=job_id,
                services=services,
            )
            if job_id is not None:
                def write_callback_operation():
                    record_operation(db, job_id, "callback")
                    db.commit()
                run_write_txn(db, write_callback_operation)
                jobs.complete(db, job_id)
        except Exception as exc:
            logging.error("Background callback processing failed: %s", exc, exc_info=True)
            if job_id is not None:
                jobs.fail(db, job_id, exc)
            services.telegram.send_text(token, chat_id, "Callback processing failed; try the command again.")
        finally:
            set_panel_actor_context(None)
            set_db_connection_context(None)
            db.close()


def native_edit_committed_after_failure(db: sqlite3.Connection, job_id: int | None, exc: BaseException) -> bool:
    if job_id is None or "Telegram sendMessage failed" in str(exc):
        return False
    try:
        return operation_phase(db, job_id) == "local_committed"
    except sqlite3.OperationalError:
        logging.warning("Could not inspect native edit operation %s after failure", job_id, exc_info=True)
        return False


def process_edit_job(
    services: _BridgeServices,
    chat_id: str,
    message_id: int,
    text: str,
    model_override: str | None = None,
    job_id: int | None = None,
) -> None:
    token = services.config.bot_token
    api_key = services.config.api_key
    model = model_override or services.config.default_model
    jobs = _jobs_for_services(services)
    with chat_job_lock(chat_id):
        db = services.db_factory()
        set_db_connection_context(db)
        try:
            if job_id is not None and not jobs.start(db, job_id):
                return
            edit_telegram_user_message(
                db,
                token,
                api_key,
                chat_id,
                message_id,
                text,
                model,
                operation_id=job_id,
                memory_service=services.memory,
                persona_service=services.persona,
            )
            if job_id is not None:
                jobs.complete(db, job_id)
        except Exception as exc:
            logging.error("Background native edit failed: %s", exc, exc_info=True)
            if native_edit_committed_after_failure(db, job_id, exc):
                logging.warning("Native edit %s committed locally; suppressing rollback fallback", job_id)
                if job_id is not None:
                    jobs.complete(db, job_id)
                return
            if job_id is not None:
                jobs.fail(db, job_id, exc)
            services.telegram.send_text(token, chat_id, "Native message edit failed; the previous branch was preserved.")
        finally:
            set_db_connection_context(None)
            db.close()


def complete_update(db: sqlite3.Connection, update_id: int, offset: int) -> None:
    def write():
        db.execute("INSERT OR IGNORE INTO processed_updates(update_id,processed_at) VALUES(?,?)", (update_id, time.time()))
        set_meta(db, "telegram_offset", str(offset))
    run_write_txn(db, write)


def restore_poll_offset(db: sqlite3.Connection, fallback: int) -> int:
    try:
        return int(get_meta(db, "telegram_offset", str(fallback)) or fallback)
    except (TypeError, ValueError, sqlite3.Error):
        return int(fallback)


def _compatibility_job_service(
    background: _BackgroundRuntime,
) -> _JobService:
    return _JobService(
        enqueue_backend=enqueue_job,
        actor_backend=job_actor_id,
        schedule_backend=mark_job_scheduled,
        start_backend=mark_job_running,
        finish_backend=finish_job,
        recover_backend=recover_jobs,
        submit_chat=background.submit_chat,
        prepare_worker=globals().get("_guard_durable_worker"),
    )


def _jobs_for_services(
    services: _BridgeServices,
) -> _JobService:
    if services.jobs is not None:
        return services.jobs
    return _compatibility_job_service(services.background)


def submit_durable_chat_job(
    db: sqlite3.Connection,
    background: _BackgroundRuntime,
    label: str,
    chat_id: str,
    job_id: int,
    function,
    *args,
) -> bool:
    jobs = _compatibility_job_service(background)
    return jobs.submit(
        db,
        int(job_id),
        _JobSubmission(
            label=str(label),
            chat_id=str(chat_id),
            worker=function,
            args=tuple(args),
        ),
    )


def dispatch_recovered_jobs(
    db: sqlite3.Connection,
    services: _BridgeServices,
    fields: dict,
    *,
    recover_running: bool = True,
) -> None:
    for job_id, chat_id, session_id, message_id, kind, payload_json in recover_jobs(
        db,
        recover_running=recover_running,
    ):
        try:
            payload = json.loads(payload_json)
            model_override = str(
                payload.get("model") or services.config.default_model
            )
            session_for_job = (
                None if payload.get("resolve_active") else str(session_id)
            )

            if kind in {"generation", "command"}:
                worker = process_message_job
                worker_args = (
                    services,
                    fields,
                    str(chat_id),
                    str(payload["text"]),
                    int(message_id),
                    session_for_job,
                    model_override,
                )
            elif kind == "callback":
                worker = process_callback_job
                worker_args = (
                    services,
                    str(chat_id),
                    payload["callback"],
                )
            elif kind == "edit":
                worker = process_edit_job
                worker_args = (
                    services,
                    str(chat_id),
                    int(message_id),
                    str(payload["text"]),
                    model_override,
                )
            elif kind == "voice":
                worker = process_voice_job
                worker_args = (
                    services,
                    fields,
                    str(chat_id),
                    payload["voice"],
                    int(message_id),
                    None if payload.get("resolve_active") else session_for_job,
                    model_override,
                )
            elif kind == "image":
                worker = process_image_job
                worker_args = (
                    services,
                    str(chat_id),
                    str(payload["file_id"]),
                    str(payload.get("caption") or ""),
                    int(payload.get("file_size") or 0),
                    int(message_id),
                    None if payload.get("resolve_active") else session_for_job,
                    model_override,
                )
            elif kind == "document":
                worker = process_document_job
                worker_args = (
                    services,
                    str(chat_id),
                    payload["document"],
                    int(message_id),
                    model_override,
                )
            else:
                finish_job(
                    db,
                    int(job_id),
                    "failed",
                    "unsupported recovered job kind",
                )
                continue

            queued = submit_durable_chat_job(
                db,
                services.background,
                kind,
                str(chat_id),
                int(job_id),
                worker,
                *worker_args,
            )
            if not queued:
                logging.warning(
                    "Could not dispatch recovered %s job %s",
                    kind,
                    job_id,
                )
        except Exception as exc:
            finish_job(db, int(job_id), "failed", str(exc))
            logging.error("Could not recover job %s", job_id, exc_info=True)


def make_durable_backlog_dispatcher(
    services: _BridgeServices,
    fields: dict,
):
    def dispatch() -> None:
        db = services.db_factory()
        try:
            dispatch_recovered_jobs(
                db,
                services,
                fields,
                recover_running=False,
            )
        finally:
            db.close()

    return dispatch


def _require_runtime_configuration(model: str) -> None:
    if not DEFAULT_CHARACTER_FILE:
        raise SystemExit("required SILLYTAVERN_DEFAULT_CHARACTER is missing from .env")
    if not model:
        raise SystemExit("required SILLYTAVERN_MODEL is missing from .env")
    if not CARD_FILE.is_file():
        raise SystemExit(f"configured default character card does not exist: {CARD_FILE}")


def _load_startup_config(environ) -> _BridgeConfig:
    config = _load_bridge_config_value(
        environ,
        character_dir=CHARACTER_DIR,
        db_file=DB_FILE,
    )
    try:
        _validate_bridge_config_value(config)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return config


def _build_startup_services(
    config: _BridgeConfig,
) -> _BridgeServices:
    group_director = _GroupDirectorService(
        load_group_state=group_state,
        safe_character=safe_character_path,
        member_labels=group_member_labels,
        card_fields=card_fields_from_file,
        generation_settings=get_generation_settings,
        generate_text=generate_text,
        director_customization=_get_director_customization_value,
        default_model=config.default_model,
    )
    memory = _MemoryService(
        recall_context=recall_memory_context,
        summary_for_prompt=session_summary_for_prompt,
        summary_state=get_session_summary,
        retain_session=retain_session_memory,
        purge_session_memory=purge_hindsight_session,
    )
    persona = _PersonaService(
        load_personas=load_personas,
        load_default_persona=default_persona_id,
        upsert_persona=upsert_native_persona,
        delete_persona=delete_native_persona,
        update_session_persona=update_session,
        persona_reference_count=_count_persona_references,
        persona_edit_lock=lambda: PERSONA_EDIT_LOCK,
    )
    sync = _SyncService(
        load_binding=sync_binding,
        count_messages=_count_session_messages,
        sync_now_backend=phase3_sync_now,
        toggle_realtime_backend=phase3_toggle_realtime,
        poll_backend=phase3_sync_poll,
        disable_realtime=_phase3_disable,
        api_configured=phase3_api_configured,
        expected_errors=(SillyTavernApiError, ValueError),
    )
    background = _BackgroundRuntime(
        submit_chat=submit_chat_background,
        register_backlog_dispatcher=register_durable_backlog_dispatcher,
        begin_shutdown=begin_background_shutdown,
    )
    jobs = _JobService(
        enqueue_backend=enqueue_job,
        actor_backend=job_actor_id,
        schedule_backend=mark_job_scheduled,
        start_backend=mark_job_running,
        finish_backend=finish_job,
        recover_backend=recover_jobs,
        submit_chat=background.submit_chat,
        prepare_worker=globals().get("_guard_durable_worker"),
    )
    return _build_bridge_services_value(
        config,
        db_factory=_partial(db_connect, config.db_file),
        telegram=_TelegramRuntime(
            request=telegram_request,
            send_text=send_text,
        ),
        background=background,
        group_director=group_director,
        memory=memory,
        persona=persona,
        sync=sync,
        jobs=jobs,
    )


def run_check(services: _BridgeServices) -> int:
    config = services.config
    fields = card_fields(read_png_chara(config.card_file))
    if phase3_api_configured():
        try:
            phase3_client().authenticate()
        except (SillyTavernApiError, ValueError) as exc:
            raise SystemExit(f"Live Sync check failed: {exc}") from exc
    me = services.telegram.request(
        config.bot_token,
        "getMe",
    )
    print(
        f"card={fields['name']}; "
        f"telegram=@{me.get('username')}; "
        f"model={config.default_model}; "
        f"db={config.db_file}"
    )
    print("check=ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    load_env_file()
    refresh_phase3_config()
    enforce_runtime_permissions()

    config = _load_startup_config(os.environ)
    try:
        validate_startup_credential(config.default_model)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    services = _build_startup_services(config)
    token = config.bot_token
    model = config.default_model
    set_bot_commands(token)

    if args.check:
        return run_check(services)

    fields = card_fields(read_png_chara(config.card_file))
    _SHUTDOWN_EVENT.clear()
    install_bridge_signal_handlers(
        services.background.begin_shutdown
    )
    db = services.db_factory()
    start_phase3_sync_worker(sync_service=services.sync)
    services.background.register_backlog_dispatcher(
        make_durable_backlog_dispatcher(
            services,
            fields,
        )
    )
    dispatch_recovered_jobs(
        db,
        services,
        fields,
    )
    offset = int(get_meta(db, "telegram_offset", "0"))
    permitted = config.allowed_users
    logging.info("Bridge started")
    last_safe_offset = offset
    while not _SHUTDOWN_EVENT.is_set():
        try:
            updates = services.telegram.request(token, "getUpdates", {"offset": offset, "timeout": 50, "allowed_updates": ["message", "edited_message", "callback_query"]})
            for update in updates:
                update_id = int(update["update_id"])
                last_safe_offset = offset
                offset = max(offset, update_id + 1)
                if db.execute("SELECT 1 FROM processed_updates WHERE update_id=?", (update_id,)).fetchone():
                    complete_update(db, update_id, offset)
                    continue
                callback = update.get("callback_query")
                if callback:
                    sender = str((callback.get("from") or {}).get("id", ""))
                    callback_message = callback.get("message") or {}
                    callback_real_chat_id = str((callback_message.get("chat") or {}).get("id", ""))
                    callback_chat_id = topic_scope_from_message(callback_real_chat_id, callback_message)
                    if callback_chat_id:
                        callback_message.setdefault("chat", {})["id"] = callback_chat_id
                    if sender in permitted and callback_chat_id:
                        if is_help_callback(str(callback.get("data") or "")):
                            handle_help_callback(
                                db,
                                token,
                                callback,
                                answer_callback,
                                str(callback.get("data") or ""),
                                callback_chat_id,
                                callback_message,
                                {},
                                "",
                                None,
                            )
                        else:
                            callback["_queued"] = True
                            callback_session = ensure_session(db, callback_chat_id, model)["session_id"]
                            callback_message_id = int(callback_message.get("message_id") or 0)
                            job_id = services.jobs.enqueue(
                                db,
                                update_id,
                                callback_chat_id,
                                callback_session,
                                callback_message_id,
                                "callback",
                                {
                                    "callback": callback,
                                    "model": model,
                                    "actor_id": sender,
                                },
                            )
                            services.jobs.submit(
                                db,
                                job_id,
                                _JobSubmission(
                                    label="callback",
                                    chat_id=callback_chat_id,
                                    worker=process_callback_job,
                                    args=(
                                        services,
                                        callback_chat_id,
                                        callback,
                                    ),
                                ),
                            )
                            answer_callback(token, str(callback.get("id", "")), "Queued")
                        complete_update(db, update_id, offset)
                    else:
                        complete_update(db, update_id, offset)
                    continue

                edited_message = update.get("edited_message")
                if edited_message:
                    edited_sender = str((edited_message.get("from") or {}).get("id", ""))
                    edited_real_chat_id = str((edited_message.get("chat") or {}).get("id", ""))
                    edited_chat_id = topic_scope_from_message(edited_real_chat_id, edited_message)
                    edited_text = edited_message.get("text")
                    if edited_sender in permitted and edited_chat_id and edited_text:
                        edited_message_id = int(edited_message.get("message_id") or 0)
                        edited_session_id = ensure_session(db, edited_chat_id, model)["session_id"]
                        job_id = services.jobs.enqueue(
                            db,
                            update_id,
                            edited_chat_id,
                            edited_session_id,
                            edited_message_id,
                            "edit",
                            {
                                "text": str(edited_text)[:12000],
                                "model": model,
                                "actor_id": edited_sender,
                            },
                        )
                        services.jobs.submit(
                            db,
                            job_id,
                            _JobSubmission(
                                label="edit",
                                chat_id=edited_chat_id,
                                worker=process_edit_job,
                                args=(
                                    services,
                                    edited_chat_id,
                                    edited_message_id,
                                    str(edited_text)[:12000],
                                    None,
                                ),
                            ),
                        )
                        services.telegram.send_text(token, edited_chat_id, "✏️ Edit queued; previous branch will be preserved until regeneration succeeds.")
                    complete_update(db, update_id, offset)
                    continue

                message = update.get("message") or {}
                sender = str((message.get("from") or {}).get("id", ""))
                real_chat_id = str((message.get("chat") or {}).get("id", ""))
                chat_id = topic_scope_from_message(real_chat_id, message)
                document = message.get("document")
                photos = message.get("photo") or []
                voice = message.get("voice") or message.get("audio")
                text = message.get("text")
                caption = str(message.get("caption") or "")
                if not chat_id:
                    continue
                if sender not in permitted:
                    logging.warning("Rejected Telegram user %s", sender)
                    services.telegram.send_text(token, chat_id, "This bot is private.")
                    complete_update(db, update_id, offset)
                    continue
                if voice:
                    message_id = int(message.get("message_id"))
                    queued_session_id = ensure_session(db, chat_id, model)["session_id"]
                    job_id = services.jobs.enqueue(
                        db,
                        update_id,
                        chat_id,
                        queued_session_id,
                        message_id,
                        "voice",
                        {
                            "voice": voice,
                            "model": model,
                            "resolve_active": True,
                            "actor_id": sender,
                        },
                    )
                    queued = services.jobs.submit(
                        db,
                        job_id,
                        _JobSubmission(
                            label="voice",
                            chat_id=chat_id,
                            worker=process_voice_job,
                            args=(
                                services,
                                fields,
                                chat_id,
                                voice,
                                message_id,
                                None,
                                None,
                            ),
                        ),
                    )
                    if queued:
                        services.telegram.send_text(token, chat_id, "🎙️ Voice queued for transcription.")
                    else:
                        services.telegram.send_text(token, chat_id, "🎙️ Voice saved for processing after restart.")
                    complete_update(db, update_id, offset)
                    continue
                if photos:
                    largest = photos[-1]
                    message_id = int(message.get("message_id"))
                    queued_session_id = ensure_session(db, chat_id, model)["session_id"]
                    job_id = services.jobs.enqueue(
                        db,
                        update_id,
                        chat_id,
                        queued_session_id,
                        message_id,
                        "image",
                        {
                            "file_id": str(largest.get("file_id", "")),
                            "caption": caption,
                            "file_size": int(largest.get("file_size") or 0),
                            "model": model,
                            "resolve_active": True,
                            "actor_id": sender,
                        },
                    )
                    queued = services.jobs.submit(
                        db,
                        job_id,
                        _JobSubmission(
                            label="image",
                            chat_id=chat_id,
                            worker=process_image_job,
                            args=(
                                services,
                                chat_id,
                                str(largest.get("file_id", "")),
                                caption,
                                int(largest.get("file_size") or 0),
                                message_id,
                                None,
                                None,
                            ),
                        ),
                    )
                    services.telegram.send_text(token, chat_id, "🖼️ Image queued for analysis." if queued else "🖼️ Image saved for processing after restart.")
                    complete_update(db, update_id, offset)
                    continue
                if document and Path(str(document.get("file_name") or "")).suffix.casefold() != ".png" and str(document.get("mime_type") or "").startswith("image/"):
                    message_id = int(message.get("message_id"))
                    queued_session_id = ensure_session(db, chat_id, model)["session_id"]
                    job_id = services.jobs.enqueue(
                        db,
                        update_id,
                        chat_id,
                        queued_session_id,
                        message_id,
                        "image",
                        {
                            "file_id": str(document.get("file_id", "")),
                            "caption": caption,
                            "file_size": int(document.get("file_size") or 0),
                            "model": model,
                            "resolve_active": True,
                            "actor_id": sender,
                        },
                    )
                    queued = services.jobs.submit(
                        db,
                        job_id,
                        _JobSubmission(
                            label="image",
                            chat_id=chat_id,
                            worker=process_image_job,
                            args=(
                                services,
                                chat_id,
                                str(document.get("file_id", "")),
                                caption,
                                int(document.get("file_size") or 0),
                                message_id,
                                None,
                                None,
                            ),
                        ),
                    )
                    services.telegram.send_text(token, chat_id, "🖼️ Image queued for analysis." if queued else "🖼️ Image saved for processing after restart.")
                    complete_update(db, update_id, offset)
                    continue
                if document:
                    message_id = int(message.get("message_id"))
                    queued_session_id = ensure_session(db, chat_id, model)["session_id"]
                    job_id = services.jobs.enqueue(
                        db,
                        update_id,
                        chat_id,
                        queued_session_id,
                        message_id,
                        "document",
                        {
                            "document": document,
                            "model": model,
                            "resolve_active": True,
                            "actor_id": sender,
                        },
                    )
                    queued = services.jobs.submit(
                        db,
                        job_id,
                        _JobSubmission(
                            label="document",
                            chat_id=chat_id,
                            worker=process_document_job,
                            args=(
                                services,
                                chat_id,
                                document,
                                message_id,
                                None,
                            ),
                        ),
                    )
                    services.telegram.send_text(token, chat_id, "📄 Document queued for character-card processing or Data Bank indexing." if queued else "📄 Document saved for processing after restart.")
                    complete_update(db, update_id, offset)
                    continue
                if not text:
                    complete_update(db, update_id, offset)
                    continue
                if send_help_command(token, chat_id, str(text)):
                    complete_update(db, update_id, offset)
                    continue
                message_id = int(message.get("message_id"))
                queued_session_id = ensure_session(db, chat_id, model)["session_id"]
                normalized_text = str(text).strip().casefold()
                is_plain_start = normalized_text == "start"
                if not str(text).lstrip().startswith("/") and not is_plain_start and not group_user_turn_allowed(db, chat_id, queued_session_id, sender):
                    services.telegram.send_text(token, chat_id, "It is not your turn in manual group mode.")
                    complete_update(db, update_id, offset)
                    continue
                if is_plain_start or is_long_running_command(str(text)):
                    job_id = services.jobs.enqueue(
                        db,
                        update_id,
                        chat_id,
                        queued_session_id,
                        message_id,
                        "command",
                        {
                            "text": str(text),
                            "model": model,
                            "resolve_active": True,
                            "actor_id": sender,
                        },
                    )
                    queued = services.jobs.submit(
                        db,
                        job_id,
                        _JobSubmission(
                            label="command",
                            chat_id=chat_id,
                            worker=process_message_job,
                            args=(
                                services,
                                fields,
                                chat_id,
                                str(text),
                                message_id,
                                None,
                                None,
                            ),
                        ),
                    )
                    services.telegram.send_text(token, chat_id, "⏳ Command queued." if queued else "⏳ Command saved for execution after restart.")
                elif str(text).lstrip().startswith("/"):
                    job_id = services.jobs.enqueue(
                        db,
                        update_id,
                        chat_id,
                        queued_session_id,
                        message_id,
                        "command",
                        {
                            "text": str(text),
                            "model": model,
                            "resolve_active": True,
                            "actor_id": sender,
                        },
                    )
                    services.jobs.submit(
                        db,
                        job_id,
                        _JobSubmission(
                            label="command",
                            chat_id=chat_id,
                            worker=process_message_job,
                            args=(
                                services,
                                fields,
                                chat_id,
                                str(text),
                                message_id,
                                None,
                                None,
                            ),
                        ),
                    )
                    services.telegram.send_text(token, chat_id, "⏳ Command queued.")
                else:
                    job_id = services.jobs.enqueue(
                        db,
                        update_id,
                        chat_id,
                        queued_session_id,
                        message_id,
                        "generation",
                        {
                            "text": str(text),
                            "model": model,
                            "resolve_active": True,
                            "actor_id": sender,
                        },
                    )
                    queued = services.jobs.submit(
                        db,
                        job_id,
                        _JobSubmission(
                            label="generation",
                            chat_id=chat_id,
                            worker=process_message_job,
                            args=(
                                services,
                                fields,
                                chat_id,
                                str(text),
                                message_id,
                                None,
                                None,
                            ),
                        ),
                    )
                    services.telegram.send_text(token, chat_id, "⏳ Message queued for generation." if queued else "⏳ Message saved for generation after restart.")
                complete_update(db, update_id, offset)
        except urllib.error.HTTPError as exc:
            logging.error("Telegram HTTP error: %s", exc.code)
            _SHUTDOWN_EVENT.wait(10)
        except KeyboardInterrupt:
            request_bridge_shutdown(
                services.background.begin_shutdown
            )
            break
        except Exception as exc:
            if _SHUTDOWN_EVENT.is_set():
                break
            offset = restore_poll_offset(db, last_safe_offset)
            logging.error("Polling error: %s", exc, exc_info=True)
            _SHUTDOWN_EVENT.wait(5)

    request_bridge_shutdown(
        services.background.begin_shutdown
    )
    sync_stopped = stop_phase3_sync_worker(timeout=5.0)
    drained = shutdown_background_executors(timeout=20.0)
    db.close()
    # Explicit maintenance on a dedicated connection after executors drained:
    # VACUUM never competes with durable job transitions on the live handle.
    run_database_maintenance()
    if not sync_stopped:
        logging.warning("Realtime sync worker did not stop before shutdown deadline")
    if not drained:
        logging.warning("Background jobs exceeded the graceful shutdown deadline")
    logging.info("Bridge stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
