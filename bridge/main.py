_SHUTDOWN_EVENT = threading.Event()


def request_bridge_shutdown(signum=None, _frame=None) -> None:
    if signum is not None:
        logging.info("Bridge shutdown requested by signal %s", signum)
    _SHUTDOWN_EVENT.set()
    begin_background_shutdown()


def install_bridge_signal_handlers() -> None:
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(signum, request_bridge_shutdown)
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


def process_message_job(token: str, api_key: str, model: str, fields: dict, chat_id: str, text: str, message_id: int, queued_session_id: str | None = None, job_id: int | None = None) -> None:
    with chat_job_lock(chat_id):
        db = db_connect()
        set_db_connection_context(db)
        try:
            if job_id is not None and not mark_job_running(db, job_id):
                return
            set_panel_actor_context(job_actor_id(db, job_id))
            existing = committed_assistant_for_message(db, chat_id, message_id)
            if existing:
                recovery_session = load_session(db, chat_id, queued_session_id, model) if queued_session_id else ensure_session(db, chat_id, model)
                if group_current_speaker(db, chat_id, recovery_session, text):
                    advance_group_turn(db, chat_id, recovery_session["session_id"], operation_id=job_id)
                if json.loads(existing[2] or "[]"):
                    clear_failed_turn(db, chat_id, message_id)
                    if job_id is not None:
                        finish_job(db, job_id, "done")
                    return
                delivery_session_id = queued_session_id or ensure_session(db, chat_id, model)["session_id"]
                send_reply(token, chat_id, str(existing[1]), db, delivery_session_id, int(existing[0]))
                clear_failed_turn(db, chat_id, message_id)
                if job_id is not None:
                    finish_job(db, job_id, "done")
                return
            process_message(db, token, api_key, model, fields, chat_id, text, message_id, queued_session_id=queued_session_id, operation_id=job_id)
            if job_id is not None:
                finish_job(db, job_id, "done")
        except Exception as exc:
            logging.error("Background message processing failed: %s", exc, exc_info=True)
            record_failed_turn(db, chat_id, message_id, text, model, str(exc), queued_session_id or "")
            if job_id is not None:
                finish_job(db, job_id, "failed", str(exc))
            if str(text).lstrip().startswith("/"):
                if "Telegram sendMessage failed" in str(exc):
                    failure_message = "Telegram could not deliver this command. The character backend was not called; retry the command."
                else:
                    failure_message = "The command failed. Use /status for details, then retry the command."
            else:
                failure_message = "The character backend failed for this message. Use /retry or /status."
            send_text(token, chat_id, failure_message)
        finally:
            set_panel_actor_context(None)
            set_db_connection_context(None)
            db.close()


def process_image_job(token: str, chat_id: str, file_id: str, caption: str, model: str, file_size: int, message_id: int, queued_session_id: str | None = None, job_id: int | None = None) -> None:
    with chat_job_lock(chat_id):
        db = db_connect()
        set_db_connection_context(db)
        try:
            if job_id is not None and not mark_job_running(db, job_id):
                return
            existing = committed_assistant_for_message(db, chat_id, message_id)
            if existing:
                if json.loads(existing[2] or "[]"):
                    clear_failed_turn(db, chat_id, message_id)
                    if job_id is not None:
                        finish_job(db, job_id, "done")
                    return
                delivery_session_id = queued_session_id or ensure_session(db, chat_id, model)["session_id"]
                send_reply(token, chat_id, str(existing[1]), db, delivery_session_id, int(existing[0]))
                clear_failed_turn(db, chat_id, message_id)
                if job_id is not None:
                    finish_job(db, job_id, "done")
                return
            process_telegram_image(db, token, chat_id, file_id, caption, model, file_size, message_id, queued_session_id=queued_session_id)
            if job_id is not None:
                finish_job(db, job_id, "done")
        except Exception as exc:
            logging.error("Background image processing failed: %s", exc, exc_info=True)
            if job_id is not None:
                finish_job(db, job_id, "failed", str(exc))
            send_text(token, chat_id, "Image processing failed. The selected model may not support vision.")
        finally:
            set_db_connection_context(None)
            db.close()


def process_callback_job(token: str, chat_id: str, callback: dict, job_id: int | None = None) -> None:
    with chat_job_lock(chat_id):
        db = db_connect()
        set_db_connection_context(db)
        try:
            if job_id is not None and not mark_job_running(db, job_id):
                return
            set_panel_actor_context(str((callback.get("from") or {}).get("id", "")))
            if job_id is not None and operation_was_applied(db, job_id):
                finish_job(db, job_id, "done")
                return
            process_callback(db, token, callback, operation_id=job_id)
            if job_id is not None:
                record_operation(db, job_id, "callback")
                db.commit()
                finish_job(db, job_id, "done")
        except Exception as exc:
            logging.error("Background callback processing failed: %s", exc, exc_info=True)
            if job_id is not None:
                finish_job(db, job_id, "failed", str(exc))
            send_text(token, chat_id, "Callback processing failed; try the command again.")
        finally:
            set_panel_actor_context(None)
            set_db_connection_context(None)
            db.close()


def process_edit_job(token: str, api_key: str, chat_id: str, message_id: int, text: str, default_model: str, job_id: int | None = None) -> None:
    with chat_job_lock(chat_id):
        db = db_connect()
        set_db_connection_context(db)
        try:
            if job_id is not None and not mark_job_running(db, job_id):
                return
            edit_telegram_user_message(db, token, api_key, chat_id, message_id, text, default_model, operation_id=job_id)
            if job_id is not None:
                finish_job(db, job_id, "done")
        except Exception as exc:
            logging.error("Background native edit failed: %s", exc, exc_info=True)
            if job_id is not None:
                finish_job(db, job_id, "failed", str(exc))
            send_text(token, chat_id, "Native message edit failed; the previous branch was preserved.")
        finally:
            set_db_connection_context(None)
            db.close()


def complete_update(db: sqlite3.Connection, update_id: int, offset: int) -> None:
    db.execute("INSERT OR IGNORE INTO processed_updates(update_id,processed_at) VALUES(?,?)", (update_id, time.time()))
    set_meta(db, "telegram_offset", str(offset))


def submit_durable_chat_job(db: sqlite3.Connection, label: str, chat_id: str, function, *args) -> bool:
    queued = submit_chat_background(label, chat_id, function, *args)
    if queued and args and isinstance(args[-1], int):
        mark_job_scheduled(db, int(args[-1]))
    return queued


def dispatch_recovered_jobs(db: sqlite3.Connection, token: str, api_key: str, default_model: str, fields: dict, recover_running: bool = True) -> None:
    for job_id, chat_id, session_id, message_id, kind, payload_json in recover_jobs(db, recover_running=recover_running):
        try:
            payload = json.loads(payload_json)
            model = str(payload.get("model") or default_model)
            session_for_job = None if payload.get("resolve_active") else str(session_id)
            if kind in {"generation", "command"}:
                text = str(payload["text"])
                queued = submit_durable_chat_job(db, kind, str(chat_id), process_message_job, token, api_key, model, fields, str(chat_id), text, int(message_id), session_for_job, int(job_id))
            elif kind == "callback":
                queued = submit_durable_chat_job(db, "callback", str(chat_id), process_callback_job, token, str(chat_id), payload["callback"], int(job_id))
            elif kind == "edit":
                queued = submit_durable_chat_job(db, "edit", str(chat_id), process_edit_job, token, api_key, str(chat_id), int(message_id), str(payload["text"]), model, int(job_id))
            elif kind == "voice":
                media_session = None if payload.get("resolve_active") else session_for_job
                queued = submit_durable_chat_job(db, "voice", str(chat_id), process_voice_job, token, api_key, model, fields, str(chat_id), payload["voice"], int(message_id), media_session, int(job_id))
            elif kind == "image":
                media_session = None if payload.get("resolve_active") else session_for_job
                queued = submit_durable_chat_job(db, "image", str(chat_id), process_image_job, token, str(chat_id), str(payload["file_id"]), str(payload.get("caption") or ""), model, int(payload.get("file_size") or 0), int(message_id), media_session, int(job_id))
            elif kind == "document":
                queued = submit_durable_chat_job(db, "document", str(chat_id), process_document_job, token, str(chat_id), payload["document"], model, int(message_id), int(job_id))
            else:
                finish_job(db, int(job_id), "failed", "unsupported recovered job kind")
                continue
            if not queued:
                logging.warning("Could not dispatch recovered %s job %s", kind, job_id)
        except Exception as exc:
            finish_job(db, int(job_id), "failed", str(exc))
            logging.error("Could not recover job %s", job_id, exc_info=True)


def make_durable_backlog_dispatcher(token: str, api_key: str, default_model: str, fields: dict):
    def dispatch() -> None:
        db = db_connect()
        try:
            dispatch_recovered_jobs(db, token, api_key, default_model, fields, recover_running=False)
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


def run_check() -> int:
    load_env_file()
    refresh_phase3_config()
    enforce_runtime_permissions()
    token = os.environ.get("SILLYTAVERN_TELEGRAM_BOT_TOKEN", "")
    model = os.environ.get("SILLYTAVERN_MODEL", DEFAULT_MODEL)
    _require_runtime_configuration(model)
    fields = card_fields(read_png_chara(CARD_FILE))
    if phase3_api_configured():
        try:
            phase3_client().authenticate()
        except (SillyTavernApiError, ValueError) as exc:
            raise SystemExit(f"Live Sync check failed: {exc}") from exc
    me = telegram_request(token, "getMe")
    print(f"card={fields['name']}; telegram=@{me.get('username')}; model={model}; db={DB_FILE}")
    print("check=ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    load_env_file()
    refresh_phase3_config()
    enforce_runtime_permissions()
    token = os.environ.get("SILLYTAVERN_TELEGRAM_BOT_TOKEN", "")
    api_key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("SILLYTAVERN_MODEL", DEFAULT_MODEL)
    _require_runtime_configuration(model)
    if not token:
        raise SystemExit("required Telegram bot token is missing from .env")
    try:
        validate_startup_credential(model)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    fields = card_fields(read_png_chara(CARD_FILE))
    set_bot_commands(token)
    if args.check:
        return run_check()
    _SHUTDOWN_EVENT.clear()
    install_bridge_signal_handlers()
    db = db_connect()
    start_phase3_sync_worker()
    register_durable_backlog_dispatcher(make_durable_backlog_dispatcher(token, api_key, model, fields))
    dispatch_recovered_jobs(db, token, api_key, model, fields)
    offset = int(get_meta(db, "telegram_offset", "0"))
    permitted = allowed_users()
    logging.info("Bridge started for card=%s model=%s", fields["name"], model)
    while not _SHUTDOWN_EVENT.is_set():
        try:
            updates = telegram_request(token, "getUpdates", {"offset": offset, "timeout": 50, "allowed_updates": ["message", "edited_message", "callback_query"]})
            for update in updates:
                update_id = int(update["update_id"])
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
                            job_id = enqueue_job(db, update_id, callback_chat_id, callback_session, callback_message_id, "callback", {"callback": callback, "model": model})
                            submit_durable_chat_job(db, "callback", callback_chat_id, process_callback_job, token, callback_chat_id, callback, job_id)
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
                        job_id = enqueue_job(db, update_id, edited_chat_id, edited_session_id, edited_message_id, "edit", {"text": str(edited_text)[:12000], "model": model, "actor_id": edited_sender})
                        submit_durable_chat_job(db, "edit", edited_chat_id, process_edit_job, token, api_key, edited_chat_id, edited_message_id, str(edited_text)[:12000], model, job_id)
                        send_text(token, edited_chat_id, "✏️ Edit queued; previous branch will be preserved until regeneration succeeds.")
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
                    send_text(token, chat_id, "This bot is private.")
                    complete_update(db, update_id, offset)
                    continue
                if voice:
                    message_id = int(message.get("message_id"))
                    queued_session_id = ensure_session(db, chat_id, model)["session_id"]
                    job_id = enqueue_job(db, update_id, chat_id, queued_session_id, message_id, "voice", {"voice": voice, "model": model, "resolve_active": True, "actor_id": sender})
                    queued = submit_durable_chat_job(db, "voice", chat_id, process_voice_job, token, api_key, model, fields, chat_id, voice, message_id, None, job_id)
                    if queued:
                        send_text(token, chat_id, "🎙️ Voice queued for transcription.")
                    else:
                        send_text(token, chat_id, "🎙️ Voice saved for processing after restart.")
                    complete_update(db, update_id, offset)
                    continue
                if photos:
                    largest = photos[-1]
                    message_id = int(message.get("message_id"))
                    queued_session_id = ensure_session(db, chat_id, model)["session_id"]
                    job_id = enqueue_job(db, update_id, chat_id, queued_session_id, message_id, "image", {"file_id": str(largest.get("file_id", "")), "caption": caption, "file_size": int(largest.get("file_size") or 0), "model": model, "resolve_active": True, "actor_id": sender})
                    queued = submit_durable_chat_job(db, "image", chat_id, process_image_job, token, chat_id, str(largest.get("file_id", "")), caption, model, int(largest.get("file_size") or 0), message_id, None, job_id)
                    send_text(token, chat_id, "🖼️ Image queued for analysis." if queued else "🖼️ Image saved for processing after restart.")
                    complete_update(db, update_id, offset)
                    continue
                if document and Path(str(document.get("file_name") or "")).suffix.casefold() != ".png" and str(document.get("mime_type") or "").startswith("image/"):
                    message_id = int(message.get("message_id"))
                    queued_session_id = ensure_session(db, chat_id, model)["session_id"]
                    job_id = enqueue_job(db, update_id, chat_id, queued_session_id, message_id, "image", {"file_id": str(document.get("file_id", "")), "caption": caption, "file_size": int(document.get("file_size") or 0), "model": model, "resolve_active": True, "actor_id": sender})
                    queued = submit_durable_chat_job(db, "image", chat_id, process_image_job, token, chat_id, str(document.get("file_id", "")), caption, model, int(document.get("file_size") or 0), message_id, None, job_id)
                    send_text(token, chat_id, "🖼️ Image queued for analysis." if queued else "🖼️ Image saved for processing after restart.")
                    complete_update(db, update_id, offset)
                    continue
                if document:
                    message_id = int(message.get("message_id"))
                    queued_session_id = ensure_session(db, chat_id, model)["session_id"]
                    job_id = enqueue_job(db, update_id, chat_id, queued_session_id, message_id, "document", {"document": document, "model": model, "resolve_active": True, "actor_id": sender})
                    queued = submit_durable_chat_job(db, "document", chat_id, process_document_job, token, chat_id, document, model, message_id, job_id)
                    send_text(token, chat_id, "📄 Document queued for character-card processing or Data Bank indexing." if queued else "📄 Document saved for processing after restart.")
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
                    send_text(token, chat_id, "It is not your turn in manual group mode.")
                    complete_update(db, update_id, offset)
                    continue
                if is_plain_start or is_long_running_command(str(text)):
                    job_id = enqueue_job(db, update_id, chat_id, queued_session_id, message_id, "command", {"text": str(text), "model": model, "resolve_active": True, "actor_id": sender})
                    queued = submit_durable_chat_job(db, "command", chat_id, process_message_job, token, api_key, model, fields, chat_id, str(text), message_id, None, job_id)
                    send_text(token, chat_id, "⏳ Command queued." if queued else "⏳ Command saved for execution after restart.")
                elif str(text).lstrip().startswith("/"):
                    job_id = enqueue_job(db, update_id, chat_id, queued_session_id, message_id, "command", {"text": str(text), "model": model, "resolve_active": True, "actor_id": sender})
                    submit_durable_chat_job(db, "command", chat_id, process_message_job, token, api_key, model, fields, chat_id, str(text), message_id, None, job_id)
                    send_text(token, chat_id, "⏳ Command queued.")
                else:
                    job_id = enqueue_job(db, update_id, chat_id, queued_session_id, message_id, "generation", {"text": str(text), "model": model, "resolve_active": True, "actor_id": sender})
                    queued = submit_durable_chat_job(db, "generation", chat_id, process_message_job, token, api_key, model, fields, chat_id, str(text), message_id, None, job_id)
                    send_text(token, chat_id, "⏳ Message queued for generation." if queued else "⏳ Message saved for generation after restart.")
                complete_update(db, update_id, offset)
        except urllib.error.HTTPError as exc:
            logging.error("Telegram HTTP error: %s", exc.code)
            _SHUTDOWN_EVENT.wait(10)
        except KeyboardInterrupt:
            request_bridge_shutdown()
            break
        except Exception as exc:
            if _SHUTDOWN_EVENT.is_set():
                break
            logging.error("Polling error: %s", exc, exc_info=True)
            _SHUTDOWN_EVENT.wait(5)

    request_bridge_shutdown()
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
