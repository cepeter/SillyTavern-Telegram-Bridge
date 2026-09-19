import sqlite3
import unittest

from bridge.memory_service import MemoryPromptContext, MemoryService


class MemoryServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.session = {"session_id": "session-1", "character_file": "mira.png"}
        self.fields = {"name": "Mira"}

    def tearDown(self):
        self.db.close()

    def _service(
        self,
        *,
        recall=None,
        summary=None,
        summary_state=None,
        retain=None,
        purge=None,
    ):
        return MemoryService(
            recall_context=recall or (lambda *_args, **_kwargs: "memory"),
            summary_for_prompt=summary or (lambda *_args, **_kwargs: "summary"),
            summary_state=summary_state or (lambda *_args, **_kwargs: ("summary", 0)),
            retain_session=retain or (lambda *_args, **_kwargs: None),
            purge_session_memory=purge or (lambda *_args, **_kwargs: 0),
        )

    def test_normal_prompt_context_combines_recall_and_summary(self):
        calls = []
        service = self._service(
            recall=lambda db, chat_id, session, fields, query: (
                calls.append(("recall", chat_id, session["session_id"], fields["name"], query))
                or "remembered fact"
            ),
            summary=lambda db, chat_id, session: (
                calls.append(("summary", chat_id, session["session_id"]))
                or "continuity summary"
            ),
            summary_state=lambda *_args: (_ for _ in ()).throw(
                AssertionError("normal prompt must not read summary state")
            ),
        )

        result = service.prompt_context(
            self.db,
            "chat",
            self.session,
            self.fields,
            "Where are we?",
        )

        self.assertEqual(
            result,
            MemoryPromptContext(
                recall="remembered fact",
                summary="continuity summary",
            ),
        )
        self.assertEqual(
            calls,
            [
                ("recall", "chat", "session-1", "Mira", "Where are we?"),
                ("summary", "chat", "session-1"),
            ],
        )

    def test_edit_covered_by_summary_suppresses_summary_without_regeneration(self):
        summary_calls = []
        service = self._service(
            recall=lambda *_args: "memory",
            summary=lambda *_args: summary_calls.append("called") or "stale summary",
            summary_state=lambda _db, chat_id, session_id: (
                "stored summary",
                42,
            ),
        )

        result = service.prompt_context(
            self.db,
            "chat",
            self.session,
            self.fields,
            "edited text",
            edited_user_rowid=20,
        )

        self.assertEqual(result.recall, "memory")
        self.assertEqual(result.summary, "")
        self.assertEqual(summary_calls, [])

    def test_edit_not_covered_by_summary_uses_normal_summary_provider(self):
        service = self._service(
            recall=lambda *_args: "memory",
            summary=lambda *_args: "safe summary",
            summary_state=lambda *_args: ("older summary", 9),
        )

        result = service.prompt_context(
            self.db,
            "chat",
            self.session,
            self.fields,
            "edited text",
            edited_user_rowid=20,
        )

        self.assertEqual(
            result,
            MemoryPromptContext(recall="memory", summary="safe summary"),
        )

    def test_retain_delegates_to_injected_provider(self):
        calls = []
        service = self._service(
            retain=lambda db, chat_id, session, fields: calls.append(
                (db, chat_id, session, fields)
            )
        )

        service.retain(self.db, "chat", self.session, self.fields)

        self.assertEqual(
            calls,
            [(self.db, "chat", self.session, self.fields)],
        )

    def test_purge_session_returns_injected_provider_result(self):
        calls = []
        service = self._service(
            purge=lambda db, chat_id, session_id: (
                calls.append((db, chat_id, session_id)) or 7
            )
        )

        result = service.purge_session(self.db, "chat", "session-1")

        self.assertEqual(result, 7)
        self.assertEqual(calls, [(self.db, "chat", "session-1")])


if __name__ == "__main__":
    unittest.main()
