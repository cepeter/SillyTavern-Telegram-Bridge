def remove_inline_keyboard(token: str, callback: dict) -> None:
    message = callback.get("message") or callback
    chat_id = str((message.get("chat") or {}).get("id", ""))
    message_id = message.get("message_id")
    if chat_id and message_id:
        close_panel_message(token, chat_id, callback)


def send_voice(token: str, chat_id: str, path: Path, caption: str = "") -> bool:
    boundary = f"----BridgeVoice{int(time.time() * 1000000)}"
    real_chat_id, thread_id = parse_topic_scope(chat_id)
    chunks = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{real_chat_id}\r\n".encode()]
    if thread_id is not None:
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"message_thread_id\"\r\n\r\n{thread_id}\r\n".encode())
    if caption:
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode())
    chunks.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"voice\"; filename=\"{path.name}\"\r\nContent-Type: audio/ogg\r\n\r\n".encode()
        + path.read_bytes() + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendVoice",
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
        return bool(result.get("ok"))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
        logging.error("Telegram voice upload failed", exc_info=True)
        return False


def synthesize_voice(text: str, output_path: Path) -> None:
    text = str(text).strip()[:TTS_MAX_CHARS]
    if not text:
        raise ValueError("TTS text is empty")
    tts_bin = os.environ.get("SILLYTAVERN_TTS_BIN", str(BRIDGE_HOME / "venv" / "bin" / "edge-tts"))
    voice = os.environ.get("SILLYTAVERN_TTS_VOICE", "id-ID-GadisNeural")
    with tempfile.TemporaryDirectory(prefix="st-tts-") as temp_dir:
        mp3 = Path(temp_dir) / "speech.mp3"
        subprocess.run([tts_bin, "--voice", voice, "--text", text, "--write-media", str(mp3)], check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-c:a", "libopus", "-b:a", "48k", str(output_path)], check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def send_tts(token: str, chat_id: str, text: str, operation_id: int | str | None = None) -> bool:
    operation_db = db_connect() if operation_id is not None else None
    try:
        if operation_db is not None:
            if operation_was_applied(operation_db, operation_id):
                return True
            if not begin_operation(operation_db, operation_id, "tts_delivery"):
                return True
        with tempfile.TemporaryDirectory(prefix="st-voice-") as temp_dir:
            voice_path = Path(temp_dir) / "reply.ogg"
            try:
                synthesize_voice(text, voice_path)
                delivered = send_voice(token, chat_id, voice_path)
                if delivered and operation_db is not None:
                    record_operation(operation_db, operation_id, "tts_delivery")
                    operation_db.commit()
                return delivered
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                logging.error("TTS generation failed", exc_info=True)
                return False
    finally:
        if operation_db is not None:
            operation_db.close()


def delete_outgoing_messages(db: sqlite3.Connection, token: str, chat_id: str, session_id: str, after_rowid: int | None = None) -> None:
    query = "SELECT telegram_message_ids FROM messages WHERE chat_id=? AND session_id=? AND role='assistant'"
    params = [chat_id, session_id]
    if after_rowid is not None:
        query += " AND rowid>?"
        params.append(after_rowid)
    for (raw_ids,) in db.execute(query, params).fetchall():
        try:
            message_ids = json.loads(raw_ids or "[]")
        except json.JSONDecodeError:
            message_ids = []
        for message_id in message_ids:
            try:
                telegram_request(token, "deleteMessage", {"chat_id": chat_id, "message_id": int(message_id)})
            except Exception:
                logging.info("Could not delete outgoing Telegram message %s", message_id, exc_info=True)


def delete_outgoing_message_row(db: sqlite3.Connection, token: str, chat_id: str, rowid: int) -> None:
    row = db.execute("SELECT telegram_message_ids FROM messages WHERE rowid=? AND chat_id=? AND role='assistant'", (rowid, chat_id)).fetchone()
    if not row:
        return
    try:
        message_ids = json.loads(row[0] or "[]")
    except json.JSONDecodeError:
        message_ids = []
    for message_id in message_ids:
        try:
            telegram_request(token, "deleteMessage", {"chat_id": chat_id, "message_id": int(message_id)})
        except Exception:
            logging.info("Could not delete outgoing Telegram message %s", message_id, exc_info=True)
    db.execute("UPDATE messages SET telegram_message_ids='[]' WHERE rowid=?", (rowid,))
    db.commit()


def quoted_speech_from_reply(text: str) -> str:
    """Return only dialogue enclosed in straight double quotes for TTS."""
    quoted = re.findall(r'"([^"\n]{1,4000})"', str(text or ""), flags=re.DOTALL)
    return re.sub(r"\s+", " ", " ".join(quoted)).strip()


def queue_user_quote_tts(token: str, chat_id: str, text: str, db: sqlite3.Connection, session_id: str, message_id: int | None = None) -> bool:
    """Queue quoted user dialogue for TTS without changing the transcript."""
    if get_meta(db, f"voice_mode:{chat_id}", "off") != "tts":
        return False
    speech = quoted_speech_from_reply(text)
    if not speech:
        return False
    stable_id = str(message_id) if message_id is not None else hashlib.sha256(f"{session_id}\0{text}".encode("utf-8")).hexdigest()[:24]
    operation_id = f"user-tts:{chat_id}:{session_id}:{stable_id}"
    if not submit_background("tts", send_tts, token, chat_id, speech, operation_id):
        logging.warning("Automatic user-quote TTS dropped for chat %s", chat_id)
        return False
    return True


def send_reply(token: str, chat_id: str, text: str, db: sqlite3.Connection | None = None, session_id: str | None = None, assistant_rowid: int | None = None) -> None:
    if db is not None and session_id:
        deliver_expression(token, chat_id, text, db, session_id)
    message_ids = send_text(token, chat_id, text)
    if db is not None and assistant_rowid is not None:
        db.execute("UPDATE messages SET telegram_message_ids=? WHERE rowid=?", (json.dumps(message_ids), assistant_rowid))
        db.commit()
    if db is not None and session_id and get_meta(db, f"voice_mode:{chat_id}", "off") == "tts":
        speech = quoted_speech_from_reply(text)
        if speech:
            operation_id = None
            if assistant_rowid is not None:
                speech_hash = hashlib.sha256(speech.encode("utf-8")).hexdigest()[:16]
                operation_id = f"assistant-tts:{chat_id}:{session_id}:{assistant_rowid}:{speech_hash}"
            if not submit_background("tts", send_tts, token, chat_id, speech, operation_id):
                logging.warning("Automatic TTS dropped for chat %s", chat_id)


_STT_MODEL_CACHE = {}
_STT_MODEL_LOCK = threading.Lock()


def transcribe_audio_bytes(raw: bytes, suffix: str = ".ogg", model_name: str | None = None, language: str | None = None) -> str:
    if len(raw) > STT_MAX_BYTES:
        raise ValueError("voice message exceeds 20 MB")
    from faster_whisper import WhisperModel
    model_name = model_name or os.environ.get("SILLYTAVERN_STT_MODEL", STT_DEFAULT_MODEL)
    model = _STT_MODEL_CACHE.get(model_name)
    if model is None:
        with _STT_MODEL_LOCK:
            model = _STT_MODEL_CACHE.get(model_name)
            if model is None:
                model = WhisperModel(model_name, device="cpu", compute_type="int8")
                _STT_MODEL_CACHE[model_name] = model
    with tempfile.NamedTemporaryFile(prefix="st-stt-", suffix=suffix, delete=True) as audio_file:
        audio_file.write(raw)
        audio_file.flush()
        options = {"beam_size": 5, "vad_filter": True}
        if language and language.casefold() not in {"auto", "detect"}:
            options["language"] = language
        segments, _info = model.transcribe(audio_file.name, **options)
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
    if not text:
        raise ValueError("voice message produced no transcription")
    return text[:12000]


def process_voice_message(db: sqlite3.Connection, token: str, api_key: str, model: str, fields: dict, chat_id: str, voice: dict, message_id: int, queued_session_id: str | None = None) -> None:
    if get_meta(db, f"stt_mode:{chat_id}", "on") != "on":
        send_text(token, chat_id, "Voice input is disabled. Use /voice_input on to enable it.")
        return
    file_size = int(voice.get("file_size") or 0)
    if file_size > STT_MAX_BYTES:
        send_text(token, chat_id, "Voice message terlalu besar. Batasnya 20 MB.")
        return
    raw = download_telegram_file(token, str(voice.get("file_id", "")), STT_MAX_BYTES)
    suffix = Path(str(voice.get("file_name") or ".ogg")).suffix or ".ogg"
    language = get_meta(db, f"stt_language:{chat_id}", "auto")
    stt_model = get_meta(db, f"stt_model:{chat_id}", STT_DEFAULT_MODEL)
    transcript = transcribe_audio_bytes(raw, suffix, stt_model, language)
    process_message(db, token, api_key, model, fields, chat_id, transcript, message_id, queued_session_id=queued_session_id)


def process_voice_job(token: str, api_key: str, model: str, fields: dict, chat_id: str, voice: dict, message_id: int, queued_session_id: str | None = None, job_id: int | None = None) -> None:
    with chat_job_lock(chat_id):
        db = db_connect()
        set_db_connection_context(db)
        try:
            if job_id is not None and not mark_job_running(db, job_id):
                return
            set_panel_actor_context(job_actor_id(db, job_id))
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
            process_voice_message(db, token, api_key, model, fields, chat_id, voice, message_id, queued_session_id=queued_session_id)
            if job_id is not None:
                finish_job(db, job_id, "done")
        except Exception as exc:
            logging.error("Voice job failed: %s", exc, exc_info=True)
            if job_id is not None:
                finish_job(db, job_id, "failed", str(exc))
            send_text(token, chat_id, "Voice processing failed. Use /voice_input status to check transcription settings.")
        finally:
            set_panel_actor_context(None)
            set_db_connection_context(None)
            db.close()


def send_typing(token: str, chat_id: str) -> None:
    try:
        telegram_request(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"})
    except Exception:
        logging.debug("typing indicator failed", exc_info=True)


def get_provider_spec(provider_id: str) -> dict:
    try:
        import yaml
        config = yaml.safe_load(PROVIDER_CONFIG_FILE.read_text(encoding="utf-8")) or {}
        return (config.get("providers") or {}).get(provider_id) or {}
    except Exception:
        logging.warning("Could not read provider spec for %s", provider_id, exc_info=True)
        return {}
