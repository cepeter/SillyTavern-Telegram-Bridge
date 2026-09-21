from bridge.memory_backend import (
    _memory_hindsight_conversation_snapshot,
    _memory_hindsight_epoch,
    _memory_hindsight_session_exists,
    _purge_hindsight_session_backend,
    _retain_session_memory_backend,
    _retain_with_client,
    _write_hindsight_successful_purge_state,
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
from bridge.extension_registry import (
    apply_summary_context_hooks as _apply_summary_context_hooks,
    run_post_retain_hooks as _run_post_retain_hooks,
    run_summary_clear_hooks as _run_summary_clear_hooks,
)
from bridge.hindsight_integrity import (
    HindsightStaleGuard as _HindsightStaleGuard,
)
from bridge.memory_service import MemoryService as _MemoryService


_HINDSIGHT_STALE_GUARD = _HindsightStaleGuard(
    open_db=lambda: db_connect(),
    session_lock=lambda chat_id, session_id: (
        hindsight_session_lock(
            chat_id,
            session_id,
        )
    ),
    memory_enabled=lambda db, chat_id: (
        memory_mode(db, chat_id) == "on"
    ),
    submit_background=(
        lambda name, fn, *args, **kwargs:
        submit_background(
            name,
            fn,
            *args,
            **kwargs,
        )
    ),
    session_exists=_memory_hindsight_session_exists,
    read_epoch=_memory_hindsight_epoch,
    snapshot=_memory_hindsight_conversation_snapshot,
    retain_backend=_retain_session_memory_backend,
    purge_backend=_purge_hindsight_session_backend,
    write_successful_purge_state=(
        _write_hindsight_successful_purge_state
    ),
    run_post_retain_hooks=_run_post_retain_hooks,
)


def retain_session_memory(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    fields: dict[str, str],
) -> None:
    _HINDSIGHT_STALE_GUARD.retain(
        db,
        chat_id,
        session,
        fields,
    )


def purge_hindsight_session(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> int:
    return _HINDSIGHT_STALE_GUARD.purge(
        db,
        chat_id,
        session_id,
    )


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
    _run_summary_clear_hooks(db, chat_id, session_id)


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
        summary_model = task_model_for_session(db, chat_id, session, "summary")
        summary = generate_text("", summary_model, summary_messages, session_id=f"summary:{chat_id}:{session['session_id']}", settings=settings).strip()[:SUMMARY_MAX_CHARS]
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
    return _apply_summary_context_hooks(summary, db, chat_id, session)


def compatibility_memory_service() -> _MemoryService:
    """Build a short-lived MemoryService from the final shared-runtime collaborators.

    This is the compatibility adapter for direct legacy callers. The function
    resolves the canonical shared-runtime collaborators at call time without
    letting application workflows call backend functions directly.
    """
    return _MemoryService(
        recall_context=recall_memory_context,
        summary_for_prompt=session_summary_for_prompt,
        summary_state=get_session_summary,
        retain_session=retain_session_memory,
        purge_session_memory=purge_hindsight_session,
    )


def resolve_memory_service(memory_service=None) -> _MemoryService:
    """Return an injected service or the compatibility adapter."""
    return memory_service if memory_service is not None else compatibility_memory_service()
