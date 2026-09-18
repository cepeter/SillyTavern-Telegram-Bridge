import json
from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class PersonaEditorTests(unittest.TestCase):
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
        rt.NATIVE_PERSONA_AVATAR_DIR = root / "User Avatars"
        rt.NATIVE_PERSONA_BACKUP_DIR = root / "backups"
        rt.NATIVE_PERSONA_AVATAR_DIR.mkdir()
        (rt.NATIVE_PERSONA_AVATAR_DIR / "user-default.png").write_bytes(b"avatar")
        self.native = {"user_avatar": "user-default.png", "power_user": {"personas": {"bridge-user.png": "Test User"}, "persona_descriptions": {"bridge-user.png": {"description": "Original description", "connections": ["keep"]}}}, "unrelated": {"keep": True}}
        rt.NATIVE_PERSONA_SETTINGS_FILE.write_text(json.dumps(self.native), encoding="utf-8")
        rt._NATIVE_PERSONA_CACHE = {}
        rt._NATIVE_PERSONA_CACHE_LAST_REFRESH = 0
        rt.phase3_api_configured = lambda: False
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.session = rt.create_session(self.db, "chat", "provider/model", session_id="persona-session")
        self.calls = []
        self.old_request = rt.telegram_request
        self.old_send_text = rt.send_text
        self.old_close = rt.close_panel_message
        rt.telegram_request = lambda _token, method, payload: self.calls.append((method, payload)) or {"message_id": 500}
        rt.send_text = lambda _token, _chat, _text: [501]
        rt.close_panel_message = lambda _token, _chat, _callback: None

    def tearDown(self):
        rt.telegram_request = self.old_request
        rt.send_text = self.old_send_text
        rt.close_panel_message = self.old_close
        rt._NATIVE_PERSONA_CACHE = self.old_cache
        rt._NATIVE_PERSONA_CACHE_LAST_REFRESH = self.old_cache_time
        rt.phase3_api_configured = self.old_phase3
        self.db.close()
        rt.DB_FILE = self.old_db
        rt.NATIVE_PERSONA_SETTINGS_FILE = self.old_settings
        rt.NATIVE_PERSONA_AVATAR_DIR = self.old_avatars
        rt.NATIVE_PERSONA_BACKUP_DIR = self.old_backups
        self.tmp.cleanup()

    def _settings(self):
        return json.loads(rt.NATIVE_PERSONA_SETTINGS_FILE.read_text(encoding="utf-8"))

    def _start(self, mode, persona_id=""):
        callback = {"id": "callback", "message": {"message_id": 77}}
        rt.start_persona_input(self.db, "token", "chat", self.session["session_id"], mode, persona_id, callback)
        return json.loads(rt.get_meta(self.db, "persona_input:chat"))

    def test_native_catalog_loads_and_create_selects_avatar(self):
        self._start("create")
        self.assertTrue(rt.handle_pending_input(self.db, "token", "chat", self.session, "writer | Writer | I write concise notes."))
        settings = self._settings()
        self.assertEqual(settings["power_user"]["personas"]["bridge-writer.png"], "Writer")
        self.assertEqual(settings["power_user"]["persona_descriptions"]["bridge-writer.png"]["description"], "I write concise notes.")
        self.assertTrue((rt.NATIVE_PERSONA_AVATAR_DIR / "bridge-writer.png").is_file())
        self.assertEqual(rt.load_session(self.db, "chat", "persona-session", "provider/model")["persona_id"], "bridge-writer.png")
        self.assertEqual(settings["unrelated"], {"keep": True})

    def test_default_persona_resolves_only_native_persona(self):
        self.assertEqual(rt.default_persona_id(), "")
        self.assertEqual(rt.persona_name(""), "")

    def test_edit_name_and_description_updates_native_settings(self):
        self._start("edit", "bridge-user.png")
        rt.handle_pending_input(self.db, "token", "chat", self.session, "Updated Name | Updated description")
        settings = self._settings()
        self.assertEqual(settings["power_user"]["personas"]["bridge-user.png"], "Updated Name")
        self.assertEqual(settings["power_user"]["persona_descriptions"]["bridge-user.png"]["description"], "Updated description")
        self.assertEqual(settings["power_user"]["persona_descriptions"]["bridge-user.png"]["connections"], ["keep"])

    def test_edit_description_only_preserves_native_name(self):
        self._start("edit_description", "bridge-user.png")
        rt.handle_pending_input(self.db, "token", "chat", self.session, "Description only")
        settings = self._settings()
        self.assertEqual(settings["power_user"]["personas"]["bridge-user.png"], "Test User")
        self.assertEqual(settings["power_user"]["persona_descriptions"]["bridge-user.png"]["description"], "Description only")

    def test_edit_callback_reads_native_metadata(self):
        rt.update_session(self.db, "chat", self.session["session_id"], persona_id="bridge-user.png")
        self.session = rt.load_session(self.db, "chat", self.session["session_id"], "provider/model")
        answers = []
        handled = rt.handle_persona_callback(self.db, "token", {"id": "cb"}, lambda _t, _i, text: answers.append(text), "persona:edit", "chat", {"message_id": 77}, self.session, "persona-session", None)
        self.assertTrue(handled)
        self.assertEqual(answers, ["Review persona"])
        self.assertIn("Original description", self.calls[-1][1]["text"])
        self.assertEqual(self.calls[-1][1]["parse_mode"], "HTML")
        self.assertIn("<pre>Original description</pre>", self.calls[-1][1]["text"])

    def test_delete_refuses_persona_referenced_by_another_chat(self):
        target = rt.upsert_native_persona("shared", "Shared", "Shared description")
        other = rt.create_session(self.db, "other-chat", "provider/model", session_id="other-session")
        rt.update_session(self.db, "other-chat", other["session_id"], persona_id=target)
        token_value = rt.dynamic_callback_token("persona", target, "chat")
        answers = []

        handled = rt.handle_persona_callback(
            self.db,
            "token",
            {"id": "cb"},
            lambda _token, _callback_id, text: answers.append(text),
            f"personadeleteconfirm:{token_value}",
            "chat",
            {"message_id": 77},
            self.session,
            self.session["session_id"],
            None,
        )

        self.assertTrue(handled)
        self.assertEqual(answers, ["Deletion refused: Persona is used by another session"])
        self.assertIsNotNone(rt.get_persona(target))

    def test_invalid_create_keeps_pending_state_and_native_file(self):
        state = self._start("create")
        self.assertTrue(rt.handle_pending_input(self.db, "token", "chat", self.session, "bad input"))
        self.assertEqual(self._settings(), self.native)
        current = json.loads(rt.get_meta(self.db, "persona_input:chat"))
        self.assertEqual(current["mode"], "create")
        self.assertEqual(current["prompt_message_ids"], state["prompt_message_ids"] + [501])

    def test_cancel_clears_pending_persona_input(self):
        self._start("create")
        self.assertTrue(rt.handle_pending_input(self.db, "token", "chat", self.session, "/cancel"))
        self.assertEqual(rt.get_meta(self.db, "persona_input:chat"), "")
        self.assertEqual(self._settings(), self.native)

    def test_pending_persona_input_is_session_scoped(self):
        self._start("create")
        other = dict(self.session)
        other["session_id"] = "other-session"
        self.assertFalse(rt.handle_pending_input(self.db, "token", "chat", other, "writer | Writer | Should not apply"))
        self.assertEqual(self._settings(), self.native)

    def test_save_failure_keeps_native_settings_and_pending_state(self):
        self._start("edit_description", "bridge-user.png")
        original_save = rt._save_native_settings
        rt._save_native_settings = lambda *_args: (_ for _ in ()).throw(RuntimeError("offline"))
        try:
            self.assertTrue(rt.handle_pending_input(self.db, "token", "chat", self.session, "Attempted update"))
        finally:
            rt._save_native_settings = original_save
        self.assertEqual(self._settings(), self.native)
        self.assertTrue(json.loads(rt.get_meta(self.db, "persona_input:chat")))


if __name__ == "__main__":
    unittest.main()
