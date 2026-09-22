from application_test_setup import ensure_application_extensions

ensure_application_extensions()

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import bridge.config as config
import bridge.memory_backend as memory_backend
import time
import bridge.callbacks as _m_callbacks
import bridge.input_flows as _m_input_flows
import bridge.main as _m_main
import bridge.memory as _m_memory
import bridge.memory_curator as _m_memory_curator
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.session_naming as _m_session_naming
class _NotFound(Exception):
    status = 404


class _FakeDocuments:
    def __init__(self, documents=None, fail_list=False):
        self.documents = dict(documents or {})
        self.fail_list = fail_list
        self.deleted = []
        self.api_client = self
        self.closed = False

    async def close(self):
        self.closed = True

    async def list_documents(self, bank_id, q=None, tags=None, tags_match=None, limit=1000, offset=0):
        if self.fail_list:
            raise RuntimeError("Hindsight unavailable")
        items = []
        for document_id, document_tags in self.documents.items():
            if q and q.casefold() not in document_id.casefold():
                continue
            if tags:
                required = set(tags)
                present = set(document_tags)
                if tags_match == "all_strict" and not required.issubset(present):
                    continue
                if tags_match != "all_strict" and not required.intersection(present):
                    continue
            items.append(SimpleNamespace(id=document_id, tags=document_tags))
        page = items[offset:offset + limit]
        return SimpleNamespace(items=page, total=len(items), limit=limit, offset=offset)

    async def delete_document(self, bank_id, document_id):
        if document_id not in self.documents:
            raise _NotFound(document_id)
        self.deleted.append(document_id)
        del self.documents[document_id]
        return SimpleNamespace(success=True)


class _FakeHindsight:
    def __init__(self, documents=None, fail_list=False):
        self.documents = _FakeDocuments(documents, fail_list=fail_list)
        self.retained = []

    def retain(self, **kwargs):
        self.retained.append(kwargs)
        document_id = kwargs["document_id"]
        self.documents.documents[document_id] = list(kwargs.get("tags") or [])
        return SimpleNamespace(success=True)


class HindsightSessionCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        self.old_client = memory_backend.hindsight_client
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()

    def tearDown(self):
        memory_backend.hindsight_client = self.old_client
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def test_document_ids_are_deterministic_and_session_prefixed(self):
        first = _m_memory.hindsight_conversation_document_id("topic:session/one")
        second = _m_memory.hindsight_conversation_document_id("topic:session/one")
        explicit = _m_memory.hindsight_explicit_document_id("topic:session/one", "remember this")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("st-session-topic-session-one-"))
        self.assertTrue(first.endswith("-conversation"))
        self.assertTrue(explicit.startswith(first.removesuffix("-conversation") + "-explicit-"))

    def test_explicit_and_conversation_retain_are_mapped_synchronously(self):
        session = _m_session_naming.create_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL, session_id="memory-session")
        fake = _FakeHindsight()
        memory_backend.hindsight_client = lambda: fake

        self.assertTrue(_m_input_flows.remember_fact(self.db, "chat", session, {"name": "Alisha"}, "remember this"))
        _m_memory._retain_session_memory_backend("chat", session, "Alisha", "conversation")

        self.assertEqual(len(fake.retained), 2)
        self.assertTrue(all(item["retain_async"] is False for item in fake.retained))
        self.assertTrue(fake.documents.closed)
        rows = self.db.execute(
            "SELECT document_id,kind FROM hindsight_documents WHERE chat_id='chat' AND session_id='memory-session' ORDER BY kind"
        ).fetchall()
        self.assertEqual([kind for _document_id, kind in rows], ["conversation", "explicit"])
        self.assertTrue(all(document_id.startswith(_m_memory_curator.hindsight_session_prefix("memory-session")) for document_id, _kind in rows))

    def test_delete_removes_mapped_tagged_prefixed_and_legacy_documents_only(self):
        active = _m_callbacks.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        target = _m_session_naming.create_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL, session_id="delete-me")
        other = _m_session_naming.create_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL, session_id="keep-me")
        prefix = _m_memory_curator.hindsight_session_prefix(target["session_id"])
        mapped_id = prefix + "-mapped"
        tagged_id = "old-explicit-target"
        prefixed_id = prefix + "-orphan"
        legacy_id = "st-session-delete-me"
        other_id = _m_memory.hindsight_conversation_document_id(other["session_id"])
        fake = _FakeHindsight({
            mapped_id: ["session:delete-me"],
            tagged_id: ["session:delete-me"],
            prefixed_id: [],
            legacy_id: ["session:delete-me"],
            other_id: ["session:keep-me"],
        })
        memory_backend.hindsight_client = lambda: fake
        self.db.execute(
            "INSERT INTO hindsight_documents(chat_id,session_id,document_id,kind,created_at) VALUES(?,?,?,?,?)",
            ("chat", "delete-me", mapped_id, "conversation", time.time()),
        )
        self.db.commit()

        deleted, reason = _m_panel_callback_routes.delete_session_data(self.db, "chat", target["session_id"], active["session_id"])

        self.assertTrue(deleted, reason)
        self.assertEqual(set(fake.documents.deleted), {mapped_id, tagged_id, prefixed_id, legacy_id})
        self.assertTrue(fake.documents.closed)
        self.assertIn(other_id, fake.documents.documents)
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM sessions WHERE chat_id='chat' AND session_id='keep-me'").fetchone())
        self.assertIsNone(self.db.execute("SELECT 1 FROM sessions WHERE chat_id='chat' AND session_id='delete-me'").fetchone())
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM hindsight_documents WHERE session_id='delete-me'").fetchone()[0], 0)

    def test_late_background_retain_skips_deleted_session(self):
        session = _m_session_naming.create_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL, session_id="gone")
        fake = _FakeHindsight()
        memory_backend.hindsight_client = lambda: fake
        self.db.execute("DELETE FROM sessions WHERE chat_id='chat' AND session_id='gone'")
        self.db.commit()

        _m_memory._retain_session_memory_backend("chat", session, "Alisha", "must not return")

        self.assertEqual(fake.retained, [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM hindsight_documents WHERE session_id='gone'").fetchone()[0], 0)

    def test_real_purge_helper_fails_closed_when_document_api_is_unavailable(self):
        _m_session_naming.create_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL, session_id="unavailable")
        fake = _FakeHindsight(fail_list=True)
        memory_backend.hindsight_client = lambda: fake

        with self.assertRaisesRegex(RuntimeError, "Hindsight session memory cleanup failed"):
            _m_main.purge_hindsight_session(self.db, "chat", "unavailable")

        self.assertTrue(fake.documents.closed)
        self.assertIsNotNone(
            self.db.execute("SELECT 1 FROM sessions WHERE chat_id='chat' AND session_id='unavailable'").fetchone()
        )

    def test_document_discovery_paginates_past_one_thousand_items(self):
        _m_session_naming.create_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL, session_id="many")
        target_docs = {
            f"legacy-explicit-{index}": ["session:many", "character:shared"]
            for index in range(1005)
        }
        target_docs["other-session"] = ["session:other", "character:shared"]
        fake = _FakeHindsight(target_docs)
        memory_backend.hindsight_client = lambda: fake

        deleted = _m_main.purge_hindsight_session(self.db, "chat", "many")

        self.assertGreaterEqual(deleted, 1005)
        self.assertTrue(fake.documents.closed)
        self.assertEqual(fake.documents.documents, {"other-session": ["session:other", "character:shared"]})


    def test_close_hindsight_client_closes_memory_and_document_api_clients_once(self):
        class _ApiClient:
            def __init__(self):
                self.closed = 0

            async def close(self):
                self.closed += 1

        memory_client = _ApiClient()
        document_client = _ApiClient()
        wrapper = SimpleNamespace(
            _memory_api=SimpleNamespace(api_client=memory_client),
            documents=SimpleNamespace(api_client=document_client),
        )

        _m_memory.close_hindsight_client(wrapper)

        self.assertEqual(memory_client.closed, 1)
        self.assertEqual(document_client.closed, 1)


if __name__ == "__main__":
    unittest.main()
