import json
from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


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



if __name__ == "__main__":
    unittest.main()
