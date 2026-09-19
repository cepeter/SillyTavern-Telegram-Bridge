from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import bridge.runtime as rt

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


class MemoryServiceMessageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = rt.DB_FILE
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.session = rt.create_session(
            self.db,
            "chat",
            "provider::model",
            session_id="memory-message",
        )
        self.fields = {"name": "Mira"}

    def tearDown(self):
        self.db.close()
        rt.DB_FILE = self.old_db
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.tmp.cleanup()

    def test_generate_and_store_reply_uses_injected_memory_service(self):
        calls = []
        captured = {}

        class FakeMemory:
            def prompt_context(
                self,
                db,
                chat_id,
                session,
                fields,
                query,
                **kwargs,
            ):
                calls.append(
                    (
                        "context",
                        db,
                        chat_id,
                        session["session_id"],
                        fields["name"],
                        query,
                        kwargs,
                    )
                )
                return MemoryPromptContext(
                    recall="service recall",
                    summary="service summary",
                )

            def retain(self, db, chat_id, session, fields):
                calls.append(
                    (
                        "retain",
                        db,
                        chat_id,
                        session["session_id"],
                        fields["name"],
                    )
                )

        def build_messages(
            session,
            fields,
            text,
            history_rows,
            **kwargs,
        ):
            captured.update(kwargs)
            return [{"role": "user", "content": text}]

        def legacy_called(*_args, **_kwargs):
            raise AssertionError("legacy memory global must not run")

        with patch.object(
            rt,
            "recall_memory_context",
            side_effect=legacy_called,
        ), patch.object(
            rt,
            "session_summary_for_prompt",
            side_effect=legacy_called,
        ), patch.object(
            rt,
            "retain_session_memory",
            side_effect=legacy_called,
        ), patch.object(
            rt,
            "rag_retrieval_bundle",
            return_value={},
        ), patch.object(
            rt,
            "rag_context_for_prompt",
            return_value="",
        ), patch.object(
            rt,
            "rag_citation_footer",
            return_value="",
        ), patch.object(
            rt,
            "build_chat_messages",
            side_effect=build_messages,
        ), patch.object(
            rt,
            "send_typing",
        ), patch.object(
            rt,
            "normalize_response_language",
            return_value="en",
        ), patch.object(
            rt,
            "get_generation_settings",
            return_value={},
        ), patch.object(
            rt,
            "generate_text",
            return_value="reply",
        ), patch.object(
            rt,
            "render_response_language",
            side_effect=lambda _key, _model, reply, *_args: reply,
        ), patch.object(
            rt,
            "save_response_variant",
            return_value=1,
        ), patch.object(
            rt,
            "queue_user_quote_tts",
        ), patch.object(
            rt,
            "send_reply",
        ):
            rt.generate_and_store_reply(
                self.db,
                "token",
                "key",
                self.fields,
                "chat",
                "hello",
                self.session,
                self.session["session_id"],
                "provider::model",
                None,
                "",
                None,
                None,
                memory_service=FakeMemory(),
            )

        self.assertEqual(captured["memory_context"], "service recall")
        self.assertEqual(captured["session_summary"], "service summary")
        self.assertEqual(calls[0][0], "context")
        self.assertEqual(calls[0][2:6], ("chat", "memory-message", "Mira", "hello"))
        self.assertEqual(calls[0][6], {})
        self.assertEqual(calls[1][0], "retain")
        self.assertEqual(calls[1][2:], ("chat", "memory-message", "Mira"))


if __name__ == "__main__":
    unittest.main()
