import json
import os
from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class PersonaEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = rt.DB_FILE
        self.original_persona = rt.PERSONA_FILE
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        rt.PERSONA_FILE = Path(self.tmp.name) / "personas.json"
        rt.PERSONA_FILE.write_text(json.dumps({
            "punto": {"name": "Punto", "description": "Original description", "tags": ["default"]},
        }), encoding="utf-8")
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
        self.db.close()
        rt.DB_FILE = self.original_db
        rt.PERSONA_FILE = self.original_persona
        self.tmp.cleanup()

    def _start(self, mode, persona_id=""):
        callback = {"id": "callback", "message": {"message_id": 77}}
        rt.start_persona_input(self.db, "token", "chat", self.session["session_id"], mode, persona_id, callback)
        return json.loads(rt.get_meta(self.db, "persona_input:chat"))

    def test_create_persona_writes_json_and_selects_it(self):
        self._start("create")
        handled = rt.handle_pending_input(self.db, "token", "chat", self.session, "writer | Writer | I write concise notes.")
        self.assertTrue(handled)
        data = json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["writer"]["name"], "Writer")
        self.assertEqual(data["writer"]["description"], "I write concise notes.")
        self.assertEqual(data["writer"]["tags"], [])
        stored = rt.load_session(self.db, "chat", "persona-session", "provider/model")
        self.assertEqual(stored["persona_id"], "writer")
        backups = list(Path(self.tmp.name).glob("personas.json.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8"))["punto"]["description"], "Original description")
        self.assertEqual(os.stat(rt.PERSONA_FILE).st_mode & 0o777, 0o600)

    def test_edit_persona_updates_name_and_description(self):
        self._start("edit", "punto")
        handled = rt.handle_pending_input(self.db, "token", "chat", self.session, "Updated Name | Updated description")
        self.assertTrue(handled)
        data = json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["punto"]["name"], "Updated Name")
        self.assertEqual(data["punto"]["description"], "Updated description")
        self.assertEqual(data["punto"]["tags"], ["default"])

    def test_edit_description_only_preserves_name_and_tags(self):
        self._start("edit", "punto")
        rt.handle_pending_input(self.db, "token", "chat", self.session, "Description only")
        data = json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["punto"], {"name": "Punto", "description": "Description only", "tags": ["default"]})

    def test_invalid_create_keeps_pending_state_and_file(self):
        state = self._start("create")
        handled = rt.handle_pending_input(self.db, "token", "chat", self.session, "bad input")
        self.assertTrue(handled)
        self.assertEqual(json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))["punto"]["description"], "Original description")
        current = json.loads(rt.get_meta(self.db, "persona_input:chat"))
        self.assertEqual(current["mode"], "create")
        self.assertEqual(current["prompt_message_ids"], state["prompt_message_ids"] + [501])
        self.assertEqual(list(Path(self.tmp.name).glob("personas.json.*.bak")), [])

    def test_duplicate_create_is_rejected(self):
        self._start("create")
        rt.handle_pending_input(self.db, "token", "chat", self.session, "punto | Other | Duplicate")
        self.assertEqual(len(json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))), 1)
        self.assertTrue(json.loads(rt.get_meta(self.db, "persona_input:chat")))

    def test_malformed_catalog_is_not_overwritten(self):
        self._start("create")
        broken = b"{not valid json"
        rt.PERSONA_FILE.write_bytes(broken)
        handled = rt.handle_pending_input(self.db, "token", "chat", self.session, "writer | Writer | Should not replace catalog")
        self.assertTrue(handled)
        self.assertEqual(rt.PERSONA_FILE.read_bytes(), broken)
        self.assertTrue(json.loads(rt.get_meta(self.db, "persona_input:chat")))

    def test_cancel_clears_pending_persona_input(self):
        self._start("create")
        handled = rt.handle_pending_input(self.db, "token", "chat", self.session, "/cancel")
        self.assertTrue(handled)
        self.assertEqual(rt.get_meta(self.db, "persona_input:chat"), "")
        self.assertEqual(json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))["punto"]["description"], "Original description")

    def test_pending_persona_input_is_session_scoped(self):
        self._start("create")
        other = dict(self.session)
        other["session_id"] = "other-session"
        handled = rt.handle_pending_input(self.db, "token", "chat", other, "writer | Writer | Should not apply")
        self.assertFalse(handled)
        self.assertEqual(rt.get_meta(self.db, "persona_input:chat"), "")
        self.assertNotIn("writer", json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8")))

    def test_missing_current_persona_cannot_start_edit(self):
        answers = []
        session = dict(self.session)
        session["persona_id"] = "missing"
        handled = rt.handle_persona_callback(self.db, "token", {"id": "cb"}, lambda _t, _i, text: answers.append(text), "persona:edit", "chat", {"message_id": 77}, session, "persona-session", None)
        self.assertTrue(handled)
        self.assertEqual(answers, ["Current persona not found"])
        self.assertEqual(rt.get_meta(self.db, "persona_input:chat"), "")


if __name__ == "__main__":
    unittest.main()
