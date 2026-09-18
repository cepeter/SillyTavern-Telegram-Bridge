from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class GroupDirectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = rt.DB_FILE
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.session = rt.create_session(
            self.db,
            "chat|topic:1",
            "provider::main",
            session_id="director-session",
            title="Director",
        )
        rt.save_group_state(
            self.db,
            "chat|topic:1",
            self.session["session_id"],
            {
                "title": "Director",
                "enabled": True,
                "turn_index": 0,
                "mode": "director",
                "forced_speaker": "",
                "members": ["alice.png", "bob.png"],
                "turn_user_id": "",
                "turn_users": [],
            },
        )

    def tearDown(self):
        self.db.close()
        rt.DB_FILE = self.old_db
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.tmp.cleanup()

    def test_parser_rejects_unknown_speaker(self):
        old_fields = rt.card_fields_from_file
        rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}
        try:
            self.assertIsNone(
                rt.parse_group_director_decision(
                    '{"speaker":"Mallory","direction":"Enter dramatically."}',
                    ["alice.png", "bob.png"],
                )
            )
        finally:
            rt.card_fields_from_file = old_fields

    def test_director_chooses_known_speaker_without_writing_transcript(self):
        old_safe = rt.safe_character_path
        old_fields = rt.card_fields_from_file
        old_generate = rt.generate_text
        rt.safe_character_path = lambda filename: Path(filename)
        rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}
        calls = []

        def fake_generate(_api_key, model, messages, **kwargs):
            calls.append((model, messages, kwargs))
            return '{"speaker":"Bob","direction":"Let Bob notice the hidden door and raise the tension."}'

        rt.generate_text = fake_generate
        try:
            before = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            plan = rt.group_director_plan(
                self.db,
                "key",
                "chat|topic:1",
                self.session,
                "What do you see?",
            )
            after = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        finally:
            rt.safe_character_path = old_safe
            rt.card_fields_from_file = old_fields
            rt.generate_text = old_generate

        self.assertEqual(plan[0], "bob.png")
        self.assertIn("hidden door", plan[2])
        self.assertEqual(before, after)
        self.assertEqual(calls[0][0], "provider::main")
        self.assertTrue(calls[0][2]["force_non_stream"])

    def test_director_falls_back_to_round_robin_on_invalid_output(self):
        old_safe = rt.safe_character_path
        old_fields = rt.card_fields_from_file
        old_generate = rt.generate_text
        rt.safe_character_path = lambda filename: Path(filename)
        rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}
        rt.generate_text = lambda *_args, **_kwargs: "not json"
        try:
            plan = rt.group_director_plan(
                self.db,
                "key",
                "chat|topic:1",
                self.session,
                "Continue.",
            )
        finally:
            rt.safe_character_path = old_safe
            rt.card_fields_from_file = old_fields
            rt.generate_text = old_generate

        self.assertEqual(plan[0], "alice.png")
        self.assertEqual(plan[2], "")


if __name__ == "__main__":
    unittest.main()
