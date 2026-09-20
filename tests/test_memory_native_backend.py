import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import bridge.extension_registry as registry
import bridge.runtime as rt


class _FakeDocuments:
    def __init__(self):
        self.documents = {}
        self.deleted = []
        self.api_client = self

    async def close(self):
        return None

    async def list_documents(
        self,
        bank_id,
        q=None,
        tags=None,
        tags_match=None,
        limit=1000,
        offset=0,
    ):
        items = []
        for document_id, document_tags in self.documents.items():
            if q and q.casefold() not in document_id.casefold():
                continue
            if (
                tags
                and not set(tags).intersection(
                    set(document_tags)
                )
            ):
                continue
            items.append(
                SimpleNamespace(
                    id=document_id,
                    tags=document_tags,
                )
            )
        page = items[offset:offset + limit]
        return SimpleNamespace(
            items=page,
            total=len(items),
            limit=limit,
            offset=offset,
        )

    async def delete_document(
        self,
        bank_id,
        document_id,
    ):
        self.deleted.append(document_id)
        self.documents.pop(document_id, None)
        return SimpleNamespace(success=True)


class _FakeHindsight:
    def __init__(self):
        self.documents = _FakeDocuments()
        self.retained = []

    def retain(self, **kwargs):
        self.retained.append(kwargs)
        self.documents.documents[
            kwargs["document_id"]
        ] = list(kwargs.get("tags") or [])
        return SimpleNamespace(success=True)


class MemoryNativeBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = rt.DB_FILE
        self.old_hindsight = rt.hindsight_client
        rt.DB_FILE = (
            Path(self.tmp.name) / "bridge.sqlite3"
        )
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.session = rt.create_session(
            self.db,
            "chat",
            rt.DEFAULT_MODEL,
            session_id="memory-native",
        )
        self.fields = {"name": "Mira"}

    def tearDown(self):
        rt.hindsight_client = self.old_hindsight
        self.db.close()
        rt.DB_FILE = self.old_db
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.tmp.cleanup()

    def _add_message(self, content="old text"):
        self.db.execute(
            "INSERT INTO messages("
            "chat_id,session_id,role,content,created_at"
            ") VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                "user",
                content,
                rt.time.time(),
            ),
        )
        self.db.commit()

    def test_memory_module_owns_guard_and_persistence_helpers(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "memory.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "_HINDSIGHT_STALE_GUARD = _HindsightStaleGuard(",
            source,
        )
        self.assertIn(
            "def _memory_hindsight_conversation_snapshot(",
            source,
        )
        self.assertIn(
            "def _memory_hindsight_epoch(",
            source,
        )
        self.assertIn(
            "def _memory_hindsight_session_exists(",
            source,
        )

    def test_malformed_and_negative_epoch_values_read_as_zero(self):
        key = (
            "hindsight_epoch:"
            "chat:"
            f"{self.session['session_id']}"
        )

        rt.set_meta(self.db, key, "not-an-int")
        self.assertEqual(
            rt._HINDSIGHT_STALE_GUARD.read_epoch(
                self.db,
                "chat",
                self.session["session_id"],
            ),
            0,
        )

        rt.set_meta(self.db, key, "-9")
        self.assertEqual(
            rt._HINDSIGHT_STALE_GUARD.read_epoch(
                self.db,
                "chat",
                self.session["session_id"],
            ),
            0,
        )

    def test_direct_guard_rejects_transcript_changed_after_queue(self):
        self._add_message()
        queued = []
        fake = _FakeHindsight()
        rt.hindsight_client = lambda: fake

        with patch.object(
            rt,
            "submit_background",
            side_effect=lambda name, fn, *args, **kwargs: (
                queued.append(
                    (name, fn, args, kwargs)
                )
            ),
        ):
            rt._HINDSIGHT_STALE_GUARD.retain(
                self.db,
                "chat",
                self.session,
                self.fields,
            )

        hindsight_jobs = [
            item
            for item in queued
            if item[0] == "hindsight_retain"
        ]
        self.assertEqual(len(hindsight_jobs), 1)
        self.db.execute(
            "UPDATE messages SET content='new text' "
            "WHERE chat_id=? AND session_id=?",
            ("chat", self.session["session_id"]),
        )
        self.db.commit()

        _name, fn, args, kwargs = hindsight_jobs[0]
        fn(*args, **kwargs)

        self.assertEqual(fake.retained, [])

    def test_successful_direct_guard_purge_advances_epoch_and_clears_mapping(self):
        self._add_message()
        mapped = rt.hindsight_conversation_document_id(
            self.session["session_id"]
        )
        self.db.execute(
            "INSERT INTO hindsight_documents("
            "chat_id,session_id,document_id,kind,created_at"
            ") VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                mapped,
                "conversation",
                rt.time.time(),
            ),
        )
        self.db.commit()

        fake = _FakeHindsight()
        fake.documents.documents[mapped] = [
            f"session:{self.session['session_id']}"
        ]
        rt.hindsight_client = lambda: fake

        old_epoch = (
            rt._HINDSIGHT_STALE_GUARD.read_epoch(
                self.db,
                "chat",
                self.session["session_id"],
            )
        )

        deleted = rt._HINDSIGHT_STALE_GUARD.purge(
            self.db,
            "chat",
            self.session["session_id"],
        )

        self.assertGreaterEqual(deleted, 1)
        self.assertEqual(
            rt._HINDSIGHT_STALE_GUARD.read_epoch(
                self.db,
                "chat",
                self.session["session_id"],
            ),
            old_epoch + 1,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM hindsight_documents "
                "WHERE chat_id=? AND session_id=?",
                ("chat", self.session["session_id"]),
            ).fetchone()[0],
            0,
        )

    def test_failed_remote_purge_preserves_epoch_and_mapping(self):
        mapped = rt.hindsight_conversation_document_id(
            self.session["session_id"]
        )
        self.db.execute(
            "INSERT INTO hindsight_documents("
            "chat_id,session_id,document_id,kind,created_at"
            ") VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                mapped,
                "conversation",
                rt.time.time(),
            ),
        )
        self.db.commit()

        key = (
            "hindsight_epoch:"
            "chat:"
            f"{self.session['session_id']}"
        )
        rt.set_meta(self.db, key, "5")

        class BrokenDocuments(_FakeDocuments):
            async def list_documents(self, *args, **kwargs):
                raise RuntimeError("remote offline")

        fake = _FakeHindsight()
        fake.documents = BrokenDocuments()
        rt.hindsight_client = lambda: fake

        with self.assertRaisesRegex(
            RuntimeError,
            "Hindsight session memory cleanup failed",
        ):
            rt._HINDSIGHT_STALE_GUARD.purge(
                self.db,
                "chat",
                self.session["session_id"],
            )

        self.assertEqual(
            rt.get_meta(self.db, key, ""),
            "5",
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM hindsight_documents "
                "WHERE chat_id=? AND session_id=?",
                ("chat", self.session["session_id"]),
            ).fetchone()[0],
            1,
        )

    def test_guard_composition_does_not_cut_over_public_ownership_early(self):
        self.assertEqual(
            Path(
                rt.retain_session_memory.__code__.co_filename
            ).name,
            "state_integrity.py",
        )
        self.assertEqual(
            Path(
                rt.purge_hindsight_session.__code__.co_filename
            ).name,
            "state_integrity.py",
        )

    def test_direct_guard_runs_post_retain_hook_when_memory_off(self):
        calls = []

        def hook(db, chat_id, session, fields):
            calls.append(
                (
                    db,
                    chat_id,
                    session["session_id"],
                    fields["name"],
                )
            )

        with patch.dict(
            registry._POST_RETAIN_HOOKS,
            {"test": hook},
            clear=True,
        ):
            rt.set_meta(
                self.db,
                "memory_mode:chat",
                "off",
            )
            rt._HINDSIGHT_STALE_GUARD.retain(
                self.db,
                "chat",
                self.session,
                self.fields,
            )

        self.assertEqual(
            calls,
            [
                (
                    self.db,
                    "chat",
                    "memory-native",
                    "Mira",
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
