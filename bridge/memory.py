def hindsight_bank_id(chat_id: str) -> str:
    return "sillytavern-telegram-" + hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:24]


def hindsight_tags(chat_id: str, session_id: str, character_name: str) -> list[str]:
    user_key = hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:24]
    character_key = "".join(ch.lower() if ch.isalnum() else "-" for ch in character_name).strip("-")[:48] or "unknown"
    return [f"user:telegram-{user_key}", f"session:{session_id}", f"character:{character_key}"]


def hindsight_client():
    from hindsight_client import Hindsight
    base_url = os.environ.get("HINDSIGHT_API_URL", HINDSIGHT_DEFAULT_URL).rstrip("/")
    validate_provider_endpoint(base_url, "SILLYTAVERN_HINDSIGHT_ALLOWED_HOSTS")
    api_key = os.environ.get("HINDSIGHT_API_KEY") or None
    return Hindsight(base_url=base_url, api_key=api_key, timeout=30.0, user_agent="SillyTavernTelegramBridge/1.0")


_HINDSIGHT_SESSION_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_HINDSIGHT_SESSION_LOCKS_GUARD = threading.Lock()


def hindsight_session_lock(chat_id: str, session_id: str) -> threading.RLock:
    key = (str(chat_id), str(session_id))
    with _HINDSIGHT_SESSION_LOCKS_GUARD:
        return _HINDSIGHT_SESSION_LOCKS.setdefault(key, threading.RLock())


def hindsight_session_prefix(session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(session_id)).strip("-.")[:80] or "session"
    digest = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:12]
    return f"st-session-{safe}-{digest}"


def hindsight_conversation_document_id(session_id: str) -> str:
    return hindsight_session_prefix(session_id) + "-conversation"


def hindsight_explicit_document_id(session_id: str, fact: str) -> str:
    digest = hashlib.sha256(fact.strip().encode("utf-8")).hexdigest()[:32]
    return hindsight_session_prefix(session_id) + "-explicit-" + digest


def _record_hindsight_document(chat_id: str, session_id: str, document_id: str, kind: str) -> None:
    mapping_db = db_connect()
    try:
        mapping_db.execute(
            "INSERT OR REPLACE INTO hindsight_documents(chat_id,session_id,document_id,kind,created_at) VALUES(?,?,?,?,?)",
            (str(chat_id), str(session_id), str(document_id), str(kind), time.time()),
        )
        mapping_db.commit()
    finally:
        mapping_db.close()


def _hindsight_not_found(exc: Exception) -> bool:
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    return status == 404 or "404" in str(exc)


async def _listed_hindsight_document_ids(api, bank_id: str, **filters) -> set[str]:
    found = set()
    offset = 0
    while True:
        result = await api.list_documents(bank_id=bank_id, limit=1000, offset=offset, **filters)
        items = list(getattr(result, "items", []) or [])
        for item in items:
            document_id = item.get("id") if isinstance(item, dict) else getattr(item, "id", "")
            if document_id:
                found.add(str(document_id))
        offset += len(items)
        if not items or offset >= int(getattr(result, "total", 0) or 0):
            break
    return found


async def _delete_hindsight_session_documents(client, bank_id: str, session_id: str, mapped_ids: set[str]) -> int:
    api = client.documents
    tag = f"session:{session_id}"
    prefix = hindsight_session_prefix(session_id)
    try:
        tagged = await _listed_hindsight_document_ids(api, bank_id, tags=[tag], tags_match="any_strict")
        prefixed = await _listed_hindsight_document_ids(api, bank_id, q=prefix)
    except Exception as exc:
        if _hindsight_not_found(exc):
            return 0
        raise
    document_ids = tagged | prefixed | set(mapped_ids) | {f"st-session-{session_id}"}
    deleted = 0
    for document_id in sorted(document_ids):
        try:
            await api.delete_document(bank_id=bank_id, document_id=document_id)
            deleted += 1
        except Exception as exc:
            if not _hindsight_not_found(exc):
                raise
    remaining = (
        await _listed_hindsight_document_ids(api, bank_id, tags=[tag], tags_match="any_strict")
        | await _listed_hindsight_document_ids(api, bank_id, q=prefix)
    )
    if remaining:
        raise RuntimeError("Hindsight session documents remain after deletion")
    return deleted


async def _delete_hindsight_session_documents_and_close(client, bank_id: str, session_id: str, mapped_ids: set[str]) -> int:
    try:
        return await _delete_hindsight_session_documents(client, bank_id, session_id, mapped_ids)
    finally:
        api_client = getattr(client.documents, "api_client", None)
        if api_client is not None:
            await api_client.close()


def purge_hindsight_session(db: sqlite3.Connection, chat_id: str, session_id: str) -> int:
    """Delete only documents attributable to one session, failing closed."""
    with hindsight_session_lock(chat_id, session_id):
        mapped_ids = {
            str(row[0]) for row in db.execute(
                "SELECT document_id FROM hindsight_documents WHERE chat_id=? AND session_id=?",
                (str(chat_id), str(session_id)),
            ).fetchall()
        }
        try:
            return asyncio.run(_delete_hindsight_session_documents_and_close(
                hindsight_client(), hindsight_bank_id(chat_id), str(session_id), mapped_ids,
            ))
        except Exception as exc:
            logging.error("Hindsight session purge failed for %s/%s", chat_id, session_id, exc_info=True)
            raise RuntimeError("Hindsight session memory cleanup failed") from exc


def memory_mode(db: sqlite3.Connection, chat_id: str) -> str:
    return get_meta(db, f"memory_mode:{chat_id}", "on")


def memory_scope(db: sqlite3.Connection, chat_id: str) -> str:
    return "session"


def memory_recall_filter(db: sqlite3.Connection, chat_id: str, session: dict[str, str], character_name: str) -> list[str]:
    tags = hindsight_tags(chat_id, session["session_id"], character_name)
    return [tags[1]]


def recall_memory_results(db: sqlite3.Connection, chat_id: str, session: dict[str, str], query: str, character_name: str = "", max_tokens: int = HINDSIGHT_RECALL_MAX_TOKENS):
    if memory_mode(db, chat_id) != "on" or not query.strip():
        return []
    try:
        client = hindsight_client()
        results = client.recall(
            bank_id=hindsight_bank_id(chat_id),
            query=query[:4000],
            max_tokens=max_tokens,
            budget="low",
            tags=memory_recall_filter(db, chat_id, session, character_name or session.get("character_file", "unknown")),
            tags_match="any_strict",
        )
        return list(getattr(results, "results", []) or [])
    except Exception:
        logging.warning("Hindsight recall unavailable for chat %s", chat_id, exc_info=True)
        return []


def recall_memory_context(db: sqlite3.Connection, chat_id: str, session: dict[str, str], fields: dict[str, str], query: str) -> str:
    results = recall_memory_results(db, chat_id, session, query, fields["name"])
    sections = []
    for result in results:
        text = str(getattr(result, "text", "") or "").strip()
        if text:
            sections.append("- " + text)
    return "\n".join(sections)[:HINDSIGHT_CONTEXT_MAX_CHARS]


def _retain_session_memory(chat_id: str, session: dict[str, str], character_name: str, conversation: str) -> None:
    session_id = str(session["session_id"])
    with hindsight_session_lock(chat_id, session_id):
        session_db = db_connect()
        try:
            exists = session_db.execute(
                "SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?",
                (str(chat_id), session_id),
            ).fetchone()
        finally:
            session_db.close()
        if not exists:
            return
        document_id = hindsight_conversation_document_id(session_id)
        try:
            client = hindsight_client()
            client.retain(
                bank_id=hindsight_bank_id(chat_id),
                content=conversation,
                context=f"SillyTavern Telegram roleplay session with character {character_name}",
                document_id=document_id,
                metadata={"source": "sillytavern_telegram_bridge", "session_id": session_id, "character": character_name},
                tags=hindsight_tags(chat_id, session_id, character_name),
                retain_async=False,
            )
            _record_hindsight_document(chat_id, session_id, document_id, "conversation")
        except Exception:
            logging.warning("Hindsight retain unavailable for chat %s", chat_id, exc_info=True)


def retain_session_memory(db: sqlite3.Connection, chat_id: str, session: dict[str, str], fields: dict[str, str]) -> None:
    if memory_mode(db, chat_id) != "on":
        return
    rows = db.execute("SELECT role,content,created_at FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?", (chat_id, session["session_id"], HINDSIGHT_RETAIN_MAX_MESSAGES)).fetchall()
    rows = list(reversed(rows))
    if not rows:
        return
    conversation = json.dumps([{"role": role, "content": content, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created_at))} for role, content, created_at in rows], ensure_ascii=False)
    submit_background("hindsight_retain", _retain_session_memory, chat_id, session, fields["name"], conversation)


def remember_fact(db: sqlite3.Connection, chat_id: str, session: dict[str, str], fields: dict[str, str], fact: str) -> bool:
    if not fact.strip():
        return False
    session_id = str(session["session_id"])
    with hindsight_session_lock(chat_id, session_id):
        if not db.execute("SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?", (str(chat_id), session_id)).fetchone():
            return False
        document_id = hindsight_explicit_document_id(session_id, fact)
        try:
            client = hindsight_client()
            client.retain(
                bank_id=hindsight_bank_id(chat_id),
                content=f"User explicitly stated: {fact.strip()[:4000]}",
                context=f"Explicit user memory request for character {fields['name']}",
                document_id=document_id,
                metadata={"source": "sillytavern_telegram_bridge", "session_id": session_id, "character": fields["name"]},
                tags=hindsight_tags(chat_id, session_id, fields["name"]),
                retain_async=False,
            )
            _record_hindsight_document(chat_id, session_id, document_id, "explicit")
            return True
        except Exception:
            logging.warning("Hindsight explicit retain unavailable for chat %s", chat_id, exc_info=True)
            return False


def handle_memory_command(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], fields: dict[str, str], command_text: str) -> None:
    parts = command_text.split(None, 2)
    argument = parts[1].casefold() if len(parts) > 1 else "status"
    if argument == "scope":
        requested_scope = parts[2].casefold() if len(parts) > 2 else ""
        if requested_scope != "session":
            send_text(token, chat_id, "Hindsight recall is fixed to the active session; broader scopes are disabled.")
            return
        send_text(token, chat_id, "Hindsight memory scope is already fixed to session.")
        return
    if argument in {"status", "on", "off"}:
        if argument in {"on", "off"}:
            set_meta(db, f"memory_mode:{chat_id}", argument)
        status = memory_mode(db, chat_id)
        send_text(token, chat_id, f"Hindsight memory: {status}\nScope: {memory_scope(db, chat_id)}\nBank: {hindsight_bank_id(chat_id)}\nRecall is hard-filtered to the active session; character tags are provenance only.")
        return
    if argument == "search":
        query = parts[2].strip() if len(parts) > 2 else ""
        if not query:
            send_text(token, chat_id, "Use /memory search <query>.")
            return
        results = recall_memory_results(db, chat_id, session, query, fields["name"], max_tokens=1600)
        lines = [str(getattr(result, "text", "") or "").strip() for result in results]
        lines = [f"- {line}" for line in lines if line][:5]
        send_text(token, chat_id, "Recalled memories:\n" + ("\n".join(lines) if lines else "No matching memories found."))
        return
    send_text(token, chat_id, "Use /memory on, /memory off, /memory status, or /memory search <query>.")


def get_session_summary(db: sqlite3.Connection, chat_id: str, session_id: str) -> tuple[str, int]:
    row = db.execute("SELECT summary,covered_until_rowid FROM session_summaries WHERE chat_id=? AND session_id=?", (chat_id, session_id)).fetchone()
    return (str(row[0]), int(row[1])) if row else ("", 0)


def clear_session_summary(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    db.execute("DELETE FROM session_summaries WHERE chat_id=? AND session_id=?", (chat_id, session_id))
    db.commit()


def transcript_for_summary(rows: list[tuple[int, str, str, float]]) -> str:
    return "\n".join(
        f"{role.upper()} ({time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(created_at))}): {content}"
        for _, role, content, created_at in rows
    )


def generate_session_summary(db: sqlite3.Connection, chat_id: str, session: dict[str, str], force: bool = False) -> str:
    rows = db.execute("SELECT rowid,role,content,created_at FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid", (chat_id, session["session_id"])).fetchall()
    if not rows:
        return ""
    existing, covered_until = get_session_summary(db, chat_id, session["session_id"])
    stable_rows = rows if force else rows[:-SUMMARY_RECENT_MESSAGES]
    if not stable_rows:
        return existing
    target_rowid = int(stable_rows[-1][0])
    if not force and existing and target_rowid <= covered_until:
        return existing
    if force:
        source = transcript_for_summary(rows)
        prompt_prefix = "Create a fresh summary from the complete transcript below."
    else:
        new_rows = [row for row in stable_rows if int(row[0]) > covered_until]
        if not new_rows or (existing and len(new_rows) < SUMMARY_UPDATE_INTERVAL):
            return existing
        source = (("Previous summary:\n" + existing + "\n\n") if existing else "") + "New transcript segment:\n" + transcript_for_summary(new_rows)
        prompt_prefix = "Update the previous summary using the new transcript segment."
    summary_messages = [
        {"role": "system", "content": "You compress a fictional roleplay chat for future continuity. Preserve current location, characters, relationships, established facts, goals, unresolved hooks, tone, and the latest scene state. Do not invent facts, do not give advice, and do not include meta commentary. Output only a concise continuity summary."},
        {"role": "user", "content": f"{prompt_prefix}\n\n{source[:50000]}"},
    ]
    settings = get_generation_settings(db, chat_id, session["session_id"])
    settings.update({"temperature": 0.2, "max_tokens": SUMMARY_MAX_OUTPUT_TOKENS, "reasoning_budget": 0})
    try:
        summary = generate_text("", session["model_id"], summary_messages, session_id=f"summary:{chat_id}:{session['session_id']}", settings=settings).strip()[:SUMMARY_MAX_CHARS]
    except Exception:
        logging.warning("Session summary generation failed for %s/%s", chat_id, session["session_id"], exc_info=True)
        return existing
    if not summary:
        return existing
    db.execute("INSERT OR REPLACE INTO session_summaries(chat_id,session_id,summary,covered_until_rowid,updated_at) VALUES(?,?,?,?,?)", (chat_id, session["session_id"], summary, target_rowid, time.time()))
    db.commit()
    return summary


def session_summary_for_prompt(db: sqlite3.Connection, chat_id: str, session: dict[str, str]) -> str:
    summary, _covered_until = get_session_summary(db, chat_id, session["session_id"])
    count = db.execute("SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session["session_id"])).fetchone()[0]
    if count >= SUMMARY_TRIGGER_MESSAGES:
        summary = generate_session_summary(db, chat_id, session)
    return summary
