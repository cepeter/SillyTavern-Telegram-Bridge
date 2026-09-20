import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest

import bridge.runtime as rt


class _FakeDocuments:
    def __init__(self):
        self.documents = {}
        self.deleted = []
        self.api_client = self
        self.closed = False

    async def close(self):
        self.closed = True

    async def list_documents(self, bank_id, q=None, tags=None, tags_match=None, limit=1000, offset=0):
        items = []
        for document_id, document_tags in self.documents.items():
            if q and q.casefold() not in document_id.casefold():
                continue
            if tags and not set(tags).intersection(set(document_tags)):
                continue
            items.append(SimpleNamespace(id=document_id, tags=document_tags))
        page = items[offset:offset + limit]
        return SimpleNamespace(items=page, total=len(items), limit=limit, offset=offset)

    async def delete_document(self, bank_id, document_id):
        self.deleted.append(document_id)
        self.documents.pop(document_id, None)
        return SimpleNamespace(success=True)


class _FakeHindsight:
    def __init__(self):
        self.documents = _FakeDocuments()
        self.retained = []

    def retain(self, **kwargs):
        self.retained.append(kwargs)
        self.documents.documents[kwargs["document_id"]] = list(kwargs.get("tags") or [])
        return SimpleNamespace(success=True)


class StateIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_db = rt.DB_FILE
        self.old_settings = rt.NATIVE_PERSONA_SETTINGS_FILE
        self.old_avatars = rt.NATIVE_PERSONA_AVATAR_DIR
        self.old_backups = rt.NATIVE_PERSONA_BACKUP_DIR
        self.old_cache = rt._NATIVE_PERSONA_CACHE
        self.old_cache_time = rt._NATIVE_PERSONA_CACHE_LAST_REFRESH
        self.old_phase3 = rt.phase3_api_configured
        self.old_hindsight = rt.hindsight_client
        rt.DB_FILE = root / "bridge.sqlite3"
        rt.NATIVE_PERSONA_SETTINGS_FILE = root / "settings.json"
        rt.NATIVE_PERSONA_AVATAR_DIR = root / "avatars"
        rt.NATIVE_PERSONA_BACKUP_DIR = root / "backups"
        rt.NATIVE_PERSONA_AVATAR_DIR.mkdir()
        (rt.NATIVE_PERSONA_AVATAR_DIR / "source.webp").write_bytes(b"webp-source-bytes")
        self.settings = {
            "user_avatar": "source.webp",
            "power_user": {
                "personas": {"native.png": "Native"},
                "persona_descriptions": {"native.png": {"description": "Native description"}},
            },
            "unrelated": {"keep": True},
        }
        rt.NATIVE_PERSONA_SETTINGS_FILE.write_text(json.dumps(self.settings), encoding="utf-8")
        rt._NATIVE_PERSONA_CACHE = {}
        rt._NATIVE_PERSONA_CACHE_LAST_REFRESH = 0
        rt.phase3_api_configured = lambda: False
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()

    def tearDown(self):
        rt.hindsight_client = self.old_hindsight
        rt.phase3_api_configured = self.old_phase3
        rt._NATIVE_PERSONA_CACHE = self.old_cache
        rt._NATIVE_PERSONA_CACHE_LAST_REFRESH = self.old_cache_time
        rt.NATIVE_PERSONA_SETTINGS_FILE = self.old_settings
        rt.NATIVE_PERSONA_AVATAR_DIR = self.old_avatars
        rt.NATIVE_PERSONA_BACKUP_DIR = self.old_backups
        self.db.close()
        rt.DB_FILE = self.old_db
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.tmp.cleanup()

    def test_live_sync_explicitly_clears_persona_and_world_and_refreshes_memory(self):
        session = rt.create_session(self.db, "chat", rt.DEFAULT_MODEL, session_id="sync-clear")
        rt.update_session(
            self.db,
            "chat",
            session["session_id"],
            persona_id="native.png",
            world_file='["old-world.json"]',
        )
        calls = []
        original_retain = rt.retain_session_memory
        original_card = rt.card_fields_from_file
        rt.retain_session_memory = lambda _db, chat_id, current, fields: calls.append(
            (chat_id, current["session_id"])
        )
        rt.card_fields_from_file = lambda _filename: {"name": "Test"}
        try:
            rt.apply_sync_snapshot(
                self.db,
                "chat",
                rt.load_session(self.db, "chat", session["session_id"], rt.DEFAULT_MODEL),
                {"name": "Remote", "persona": "", "world_info": []},
                [("user", "remote transcript")],
                {},
            )
        finally:
            rt.retain_session_memory = original_retain
            rt.card_fields_from_file = original_card
        updated = rt.load_session(self.db, "chat", session["session_id"], rt.DEFAULT_MODEL)
        self.assertEqual(updated["persona_id"], "")
        self.assertEqual(updated["world_file"], "")
        self.assertEqual(calls, [("chat", "sync-clear")])

    def test_stale_hindsight_retain_is_rejected_after_transcript_change(self):
        session = rt.create_session(self.db, "chat", rt.DEFAULT_MODEL, session_id="memory-race")
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", session["session_id"], "user", "old text", rt.time.time()),
        )
        self.db.commit()
        conversation, snapshot_hash = rt._hindsight_conversation_snapshot(
            self.db, "chat", session["session_id"]
        )
        epoch = rt._hindsight_memory_epoch(self.db, "chat", session["session_id"])
        self.db.execute(
            "UPDATE messages SET content='new text' WHERE chat_id='chat' AND session_id=?",
            (session["session_id"],),
        )
        self.db.commit()
        fake = _FakeHindsight()
        rt.hindsight_client = lambda: fake

        rt._retain_session_memory(
            "chat", session, "Alisha", conversation, snapshot_hash, epoch
        )

        self.assertEqual(fake.retained, [])

    def test_successful_hindsight_purge_invalidates_queued_retain_and_clears_mapping(self):
        session = rt.create_session(self.db, "chat", rt.DEFAULT_MODEL, session_id="memory-purge")
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", session["session_id"], "user", "old text", rt.time.time()),
        )
        mapped = rt.hindsight_conversation_document_id(session["session_id"])
        self.db.execute(
            "INSERT INTO hindsight_documents(chat_id,session_id,document_id,kind,created_at) VALUES(?,?,?,?,?)",
            ("chat", session["session_id"], mapped, "conversation", rt.time.time()),
        )
        self.db.commit()
        conversation, snapshot_hash = rt._hindsight_conversation_snapshot(
            self.db, "chat", session["session_id"]
        )
        old_epoch = rt._hindsight_memory_epoch(self.db, "chat", session["session_id"])
        fake = _FakeHindsight()
        fake.documents.documents[mapped] = [f"session:{session['session_id']}"]
        rt.hindsight_client = lambda: fake

        rt.purge_hindsight_session(self.db, "chat", session["session_id"])
        rt._retain_session_memory(
            "chat", session, "Alisha", conversation, snapshot_hash, old_epoch
        )

        self.assertEqual(fake.retained, [])
        self.assertGreater(
            rt._hindsight_memory_epoch(self.db, "chat", session["session_id"]),
            old_epoch,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM hindsight_documents WHERE chat_id='chat' AND session_id=?",
                (session["session_id"],),
            ).fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
