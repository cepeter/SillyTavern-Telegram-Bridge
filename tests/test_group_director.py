from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bridge.extension_registry as extension_registry
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

    def test_director_without_policy_uses_main_model_and_base_token_budget(self):
        old_safe = rt.safe_character_path
        old_fields = rt.card_fields_from_file
        old_generate = rt.generate_text
        calls = []

        rt.safe_character_path = lambda filename: Path(filename)
        rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}

        def fake_generate(_key, model, messages, **kwargs):
            calls.append((model, messages, kwargs))
            return '{"speaker":"Alice","direction":"Hold the beat."}'

        rt.generate_text = fake_generate
        try:
            with patch.object(
                extension_registry,
                "_DIRECTOR_CUSTOMIZATION_PROVIDER",
                None,
            ):
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
        self.assertEqual(calls[0][0], "provider::main")
        self.assertEqual(calls[0][2]["settings"]["max_tokens"], 180)

    def test_director_applies_bounded_customization(self):
        old_safe = rt.safe_character_path
        old_fields = rt.card_fields_from_file
        old_generate = rt.generate_text
        calls = []

        rt.safe_character_path = lambda filename: Path(filename)
        rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}

        def fake_generate(_key, model, messages, **kwargs):
            calls.append((model, messages, kwargs))
            return '{"speaker":"Bob","direction":"Notice the door."}'

        customization = extension_registry.DirectorCustomization(
            model="utility::director",
            hidden_instructions="Hidden scene objective: reveal the door slowly.",
            max_tokens=220,
            speaker_context="Hidden scene objective: reveal the door slowly.",
        )

        rt.generate_text = fake_generate
        try:
            with patch.object(
                extension_registry,
                "_DIRECTOR_CUSTOMIZATION_PROVIDER",
                ("test", lambda db, chat_id, session: customization),
            ):
                plan = rt.group_director_plan(
                    self.db,
                    "key",
                    "chat|topic:1",
                    self.session,
                    "Look around.",
                )
        finally:
            rt.safe_character_path = old_safe
            rt.card_fields_from_file = old_fields
            rt.generate_text = old_generate

        self.assertEqual(plan[0], "bob.png")
        self.assertEqual(calls[0][0], "utility::director")
        self.assertEqual(calls[0][2]["settings"]["max_tokens"], 220)
        joined = "\n".join(str(message["content"]) for message in calls[0][1])
        self.assertIn("reveal the door slowly", joined)

    def test_director_policy_failure_uses_ordinary_director_behavior(self):
        old_safe = rt.safe_character_path
        old_fields = rt.card_fields_from_file
        old_generate = rt.generate_text
        calls = []

        rt.safe_character_path = lambda filename: Path(filename)
        rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}

        def fail_policy(_db, _chat_id, _session):
            raise RuntimeError("policy failed")

        def fake_generate(_key, model, messages, **kwargs):
            calls.append((model, messages, kwargs))
            return '{"speaker":"Alice","direction":"Continue normally."}'

        rt.generate_text = fake_generate
        try:
            with patch.object(
                extension_registry,
                "_DIRECTOR_CUSTOMIZATION_PROVIDER",
                ("broken", fail_policy),
            ):
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
        self.assertEqual(calls[0][0], "provider::main")
        self.assertEqual(calls[0][2]["settings"]["max_tokens"], 180)

    def test_forced_speaker_bypasses_policy_and_generation(self):
        state = rt.group_state(self.db, "chat|topic:1", self.session["session_id"])
        state["forced_speaker"] = "bob.png"
        rt.save_group_state(
            self.db,
            "chat|topic:1",
            self.session["session_id"],
            state,
        )

        old_safe = rt.safe_character_path
        old_generate = rt.generate_text
        policy_calls = []
        generation_calls = []
        rt.safe_character_path = lambda filename: Path(filename)

        def policy(_db, _chat_id, _session):
            policy_calls.append(True)
            return None

        def generate(*_args, **_kwargs):
            generation_calls.append(True)
            raise AssertionError("generation should not run")

        rt.generate_text = generate
        try:
            with patch.object(
                extension_registry,
                "_DIRECTOR_CUSTOMIZATION_PROVIDER",
                ("test", policy),
            ):
                plan = rt.group_director_plan(
                    self.db,
                    "key",
                    "chat|topic:1",
                    self.session,
                    "Continue.",
                )
        finally:
            rt.safe_character_path = old_safe
            rt.generate_text = old_generate

        self.assertEqual(plan[0], "bob.png")
        self.assertEqual(policy_calls, [])
        self.assertEqual(generation_calls, [])

    def test_group_prompt_context_appends_director_policy_context(self):
        old_safe = rt.safe_character_path
        old_fields = rt.card_fields_from_file
        rt.safe_character_path = lambda filename: Path(filename)
        rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}

        customization = extension_registry.DirectorCustomization(
            speaker_context="Hidden scene objective: keep the letter unopened."
        )
        try:
            with patch.object(
                extension_registry,
                "_DIRECTOR_CUSTOMIZATION_PROVIDER",
                ("test", lambda db, chat_id, session: customization),
            ):
                context = rt.group_prompt_context(
                    self.db,
                    "chat|topic:1",
                    self.session,
                    "alice.png",
                    "Keep the pace measured.",
                )
        finally:
            rt.safe_character_path = old_safe
            rt.card_fields_from_file = old_fields

        self.assertIn("Invisible director guidance: Keep the pace measured.", context)
        self.assertIn("keep the letter unopened", context)


if __name__ == "__main__":
    unittest.main()
