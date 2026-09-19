import sqlite3
import unittest

from bridge.extension_registry import DirectorCustomization
from bridge.group_director_service import GroupDirectorService


class GroupDirectorServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute(
            "CREATE TABLE messages("
            "chat_id TEXT,session_id TEXT,role TEXT,content TEXT,created_at REAL)"
        )
        self.db.executemany(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
            "VALUES(?,?,?,?,?)",
            [
                ("chat", "session", "user", "hello", 1.0),
                ("chat", "session", "assistant", "hi", 2.0),
            ],
        )
        self.state = {
            "title": "Group",
            "enabled": True,
            "turn_index": 1,
            "mode": "director",
            "forced_speaker": "",
            "members": ["alice.png", "bob.png"],
            "turn_user_id": "",
            "turn_users": [],
        }
        self.generated = []
        self.policy_calls = []
        self.customization = None
        self.generation_result = '{"speaker":"Bob","direction":"raise the stakes"}'
        self.generation_error = None

        def generate_text(
            api_key,
            model,
            messages,
            *,
            session_id,
            settings,
            force_non_stream,
        ):
            self.generated.append(
                {
                    "api_key": api_key,
                    "model": model,
                    "messages": messages,
                    "session_id": session_id,
                    "settings": dict(settings),
                    "force_non_stream": force_non_stream,
                }
            )
            if self.generation_error is not None:
                raise self.generation_error
            return self.generation_result

        def director_customization(db, chat_id, session):
            self.policy_calls.append((db, chat_id, session))
            return self.customization

        self.service = GroupDirectorService(
            load_group_state=lambda _db, _chat_id, _session_id: dict(self.state),
            safe_character=lambda filename: filename in {"alice.png", "bob.png"},
            member_labels=lambda filenames: [
                {"alice.png": "Alice", "bob.png": "Bob"}[filename]
                for filename in filenames
            ],
            card_fields=lambda filename: {
                "name": {"alice.png": "Alice", "bob.png": "Bob"}[filename]
            },
            generation_settings=lambda _db, _chat_id, _session_id: {
                "temperature": 0.85,
                "max_tokens": 1800,
                "top_p": 1.0,
                "frequency_penalty": 0.0,
                "presence_penalty": 0.0,
                "reasoning_budget": 0,
                "stop_sequences": "",
            },
            generate_text=generate_text,
            director_customization=director_customization,
            default_model="main-model",
        )
        self.session = {
            "session_id": "session",
            "model_id": "story-model",
        }

    def tearDown(self):
        self.db.close()

    def test_plan_chooses_known_speaker_without_mutating_transcript(self):
        before = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

        result = self.service.plan(
            self.db,
            "api-key",
            "chat",
            self.session,
            "continue",
        )

        after = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        self.assertEqual(result[0], "bob.png")
        self.assertEqual(result[2], "raise the stakes")
        self.assertEqual(before, after)
        self.assertEqual(self.generated[0]["model"], "story-model")
        self.assertEqual(self.generated[0]["settings"]["max_tokens"], 180)

    def test_invalid_output_falls_back_to_round_robin(self):
        self.generation_result = '{"speaker":"Unknown","direction":"ignored"}'

        result = self.service.plan(
            self.db,
            "api-key",
            "chat",
            self.session,
            "continue",
        )

        self.assertEqual(result, ("bob.png", self.state, ""))

    def test_generation_error_falls_back_to_round_robin(self):
        self.generation_error = RuntimeError("provider down")

        result = self.service.plan(
            self.db,
            "api-key",
            "chat",
            self.session,
            "continue",
        )

        self.assertEqual(result, ("bob.png", self.state, ""))

    def test_forced_speaker_bypasses_policy_and_generation(self):
        self.state["forced_speaker"] = "alice.png"

        result = self.service.plan(
            self.db,
            "api-key",
            "chat",
            self.session,
            "continue",
        )

        self.assertEqual(result, ("alice.png", self.state, ""))
        self.assertEqual(self.policy_calls, [])
        self.assertEqual(self.generated, [])

    def test_policy_customization_is_bounded(self):
        self.customization = DirectorCustomization(
            model="utility-model",
            hidden_instructions="Move toward the hidden goal.",
            max_tokens=500,
            speaker_context="Keep the goal implicit.",
        )

        result = self.service.plan(
            self.db,
            "api-key",
            "chat",
            self.session,
            "continue",
        )

        self.assertEqual(result[0], "bob.png")
        request = self.generated[0]
        self.assertEqual(request["model"], "utility-model")
        self.assertEqual(request["settings"]["max_tokens"], 500)
        self.assertIn(
            "Hidden Director policy:\nMove toward the hidden goal.",
            request["messages"][1]["content"],
        )

    def test_prompt_context_appends_policy_speaker_context(self):
        self.customization = DirectorCustomization(
            speaker_context="Keep the goal implicit."
        )

        context = self.service.prompt_context(
            self.db,
            "chat",
            self.session,
            "alice.png",
            "slow the pacing",
        )

        self.assertIn("Current speaker: Alice", context)
        self.assertIn("Invisible director guidance: slow the pacing", context)
        self.assertIn("Keep the goal implicit.", context)


if __name__ == "__main__":
    unittest.main()
