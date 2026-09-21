from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bridge.config as config
import bridge.runtime as rt


class MemoryCuratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = rt.db_connect()
        self.session = rt.create_session(
            self.db,
            "chat",
            "primary::main",
            session_id="curator",
            title="Curator",
        )
        rt.set_task_model(self.db, "chat", self.session["session_id"], "utility::model")

    def tearDown(self):
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def _add_turn(self):
        now = rt.time.time()
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "user", "My sister is named Hana and I promised to call her Sunday.", now),
        )
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "assistant", "I'll remember that.", now + 0.001),
        )
        self.db.commit()

    def test_parser_deduplicates_by_stable_key(self):
        parsed = rt.parse_curated_memories(
            '{"memories":['
            '{"key":"family.sister","text":"Sister is Hana.","kind":"relationship","confidence":0.8},'
            '{"key":"family.sister","text":"The user\'s sister is Hana.","kind":"relationship","confidence":0.9}'
            ']}'
        )
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["key"], "family.sister")
        self.assertEqual(parsed[0]["confidence"], 0.9)

    def test_parser_generates_key_when_model_omits_one(self):
        parsed = rt.parse_curated_memories(
            '{"memories":[{"text":"The user has a sister named Hana."}]}'
        )
        self.assertEqual(len(parsed), 1)
        self.assertRegex(parsed[0]["key"], r"^fact-[0-9a-f]{12}$")

    def test_curator_uses_utility_model_and_retains_deterministic_document(self):
        self._add_turn()
        seen_models = []
        retained = []
        old_generate = rt.generate_text
        old_retain = rt._retain_with_client
        def fake_generate(_key, model, _messages, **_kwargs):
            self.assertFalse(self.db.in_transaction)
            seen_models.append(model)
            return (
                '{"memories":[{"key":"family.sister","text":"The user\'s sister is Hana.",'
                '"kind":"relationship","confidence":0.95}]}'
            )

        def fake_retain(*args, **kwargs):
            self.assertFalse(self.db.in_transaction)
            retained.append((args, kwargs))
            return True

        rt.generate_text = fake_generate
        rt._retain_with_client = fake_retain
        try:
            items = rt.curate_memory_now(
                self.db, "", "chat", self.session, "Mira"
            )
        finally:
            rt.generate_text = old_generate
            rt._retain_with_client = old_retain

        self.assertEqual(seen_models, ["utility::model"])
        self.assertEqual(items[0]["key"], "family.sister")
        self.assertEqual(retained[0][0][2], rt.curated_memory_document_id(self.session["session_id"]))
        self.assertEqual(retained[0][0][6], "curated")

    def test_retain_hook_queues_curator_only_when_memory_enabled(self):
        self._add_turn()
        queued = []
        old_submit = rt.submit_background
        rt.submit_background = lambda name, fn, *args, **kwargs: queued.append(name)
        try:
            rt.set_meta(self.db, "memory_mode:chat", "off")
            rt.retain_session_memory(self.db, "chat", self.session, {"name": "Mira"})
            self.assertNotIn("memory_curator", queued)

            rt.set_meta(self.db, "memory_mode:chat", "on")
            rt.retain_session_memory(self.db, "chat", self.session, {"name": "Mira"})
        finally:
            rt.submit_background = old_submit

        self.assertIn("memory_curator", queued)

    def test_stale_race_rechecks_inside_transaction_and_skips_retain(self):
        self._add_turn()
        target_rowid = 2
        initial_raw = rt.json.dumps({"items": [], "through_rowid": 0})
        newer_items = [
            {
                "kind": "fact",
                "key": "newer",
                "text": "Newer fact",
                "confidence": 1.0,
            }
        ]
        newer_raw = rt.json.dumps(
            {
                "items": newer_items,
                "through_rowid": target_rowid + 1,
            }
        )

        def fake_generate(_key, _model, _messages, **_kwargs):
            self.assertFalse(self.db.in_transaction)
            return (
                '{"memories":[{"kind":"fact","key":"older",'
                '"text":"Older fact","confidence":1.0}]}'
            )

        with patch.object(
            rt,
            "_repo_load_meta_value",
            side_effect=[initial_raw, newer_raw],
        ), patch.object(
            rt,
            "_repo_store_meta_value",
        ) as store_meta, patch.object(
            rt,
            "_retain_with_client",
        ) as retain, patch.object(
            rt,
            "generate_text",
            side_effect=fake_generate,
        ):
            items = rt.curate_memory_now(
                self.db,
                "",
                "chat",
                self.session,
                "Mira",
            )

        self.assertEqual(items, newer_items)
        store_meta.assert_not_called()
        retain.assert_not_called()

    def test_stale_target_does_not_regenerate_or_replace_newer_curated_state(self):
        self._add_turn()
        payload = {
            "items": [{"key": "existing", "text": "Newer fact", "kind": "fact", "confidence": 1.0}],
            "through_rowid": 999,
            "updated_at": rt.time.time(),
        }
        rt.set_meta(
            self.db,
            rt.memory_curator_key("chat", self.session["session_id"]),
            rt.json.dumps(payload),
        )
        calls = []
        old_generate = rt.generate_text
        rt.generate_text = lambda *_args, **_kwargs: calls.append(True) or '{"memories":[]}'
        try:
            items = rt.curate_memory_now(
                self.db,
                "",
                "chat",
                self.session,
                "Mira",
                through_rowid=2,
            )
        finally:
            rt.generate_text = old_generate

        self.assertEqual(calls, [])
        self.assertEqual(items[0]["key"], "existing")


if __name__ == "__main__":
    unittest.main()
