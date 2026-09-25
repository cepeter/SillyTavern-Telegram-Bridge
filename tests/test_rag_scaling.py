from application_test_setup import ensure_application_extensions
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import bridge.help as _m_help
import bridge.memory_curator as _m_memory_curator
import bridge.rag as _m_rag
import bridge.rag_core as _m_telegram
import bridge.rag_core as rag_core


class RagScalingTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = self.app_settings_builder.db_file
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.namespace = _m_rag.rag_embedding_namespace(app_settings=self.app_settings_builder.build())

    def tearDown(self):
        self.db.close()
        self.app_settings_builder.db_file = self.original_db
        self.tmp.cleanup()

    def _insert_chunks(
        self,
        count: int,
        needle_index: int | None = None,
        semantic_target_index: int | None = None,
    ):
        now = time.time()
        document_id = "doc-large"
        self.db.execute(
            "INSERT INTO data_bank_documents(chat_id,document_id,filename,byte_size,chunk_count,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("chat", document_id, "large.txt", count, count, now, now),
        )
        ids = []
        for index in range(count):
            content = f"chunk {index}"
            if index == needle_index:
                content += " needle"
            cursor = self.db.execute(
                "INSERT INTO data_bank_chunks(chat_id,document_id,chunk_index,content) VALUES(?,?,?,?)",
                ("chat", document_id, index, content),
            )
            chunk_id = int(cursor.lastrowid)
            ids.append(chunk_id)
            vector = [1.0, 0.0]
            if semantic_target_index is not None and index != semantic_target_index:
                vector = [-1.0, 0.0]
            self.db.execute(
                "INSERT INTO data_bank_embeddings("
                "chunk_id,embedding_namespace,dimensions,vector_json,vector_signature,vector_norm"
                ") VALUES(?,?,?,?,?,?)",
                (
                    chunk_id,
                    self.namespace,
                    2,
                    json.dumps(vector),
                    _m_rag.embedding_signature(vector),
                    _m_rag.embedding_norm(vector),
                ),
            )
            self.db.execute(
                "INSERT INTO data_bank_fts(content,chat_id,document_id,filename,chunk_id) VALUES(?,?,?,?,?)",
                (content, "chat", document_id, "large.txt", chunk_id),
            )
        self.db.commit()
        return ids

    def test_current_embedding_schema_rejects_missing_signature_and_norm(self):
        now = time.time()
        self.db.execute(
            "INSERT INTO data_bank_documents("
            "chat_id,document_id,filename,byte_size,chunk_count,"
            "created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?)",
            ("chat", "strict-doc", "strict.txt", 1, 1, now, now),
        )
        chunk_id = self.db.execute(
            "INSERT INTO data_bank_chunks(chat_id,document_id,chunk_index,content) VALUES(?,?,?,?)",
            ("chat", "strict-doc", 0, "strict"),
        ).lastrowid

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO data_bank_embeddings(chunk_id,embedding_namespace,dimensions,vector_json) VALUES(?,?,?,?)",
                (chunk_id, self.namespace, 2, "[1.0,0.0]"),
            )

    def test_rag_sources_have_no_legacy_backfill_or_sampling_fallback(self):
        root = Path(__file__).parents[1] / "bridge"
        core = (root / "rag_core.py").read_text(encoding="utf-8")
        retrieval = (root / "rag_retrieval.py").read_text(encoding="utf-8")
        shell = (root / "rag.py").read_text(encoding="utf-8")

        self.assertNotIn("backfill_rag_embedding_signatures", core)
        self.assertNotIn("backfill_rag_embedding_signatures", shell)
        self.assertNotIn("DEFAULT_SEMANTIC_SAMPLE_WINDOWS", retrieval)
        self.assertNotIn("sample_windows", retrieval)
        self.assertNotIn("Compatibility fallback", retrieval)

    def test_small_corpus_keeps_exact_semantic_candidate_set(self):
        ids = self._insert_chunks(20)
        candidates = _m_rag.semantic_candidate_chunk_ids(self.db, "chat", self.namespace, [], candidate_limit=64)
        self.assertEqual(candidates, tuple(ids))

    def test_large_corpus_is_bounded_and_keeps_lexical_neighborhood(self):
        ids = self._insert_chunks(500)
        hit = ids[250]
        candidates = _m_rag.semantic_candidate_chunk_ids(self.db, "chat", self.namespace, [hit], candidate_limit=64)
        self.assertLessEqual(len(candidates), 64)
        for expected in ids[248:253]:
            self.assertIn(expected, candidates)

    def test_retrieve_decodes_only_bounded_semantic_shortlist(self):
        self._insert_chunks(300, needle_index=150)
        original_cached = rag_core.cached_rag_embedding
        original_limit = rag_core.rag_semantic_candidate_limit
        original_cosine = rag_core.cosine_similarity
        cosine_calls = []
        rag_core.cached_rag_embedding = lambda _db, _query, *, app_settings=None: [1.0, 0.0]
        rag_core.rag_semantic_candidate_limit = lambda *, app_settings=None: 32

        def counted_cosine(left, right, *norms):
            cosine_calls.append((left, right))
            return original_cosine(left, right, *norms)

        rag_core.cosine_similarity = counted_cosine
        try:
            results = _m_rag.retrieve_data_bank(
                self.db, "chat", "needle", limit=5, app_settings=self.app_settings_builder.build()
            )
        finally:
            rag_core.cached_rag_embedding = original_cached
            rag_core.rag_semantic_candidate_limit = original_limit
            rag_core.cosine_similarity = original_cosine

        self.assertTrue(results)
        self.assertGreater(len(cosine_calls), 0)
        self.assertLessEqual(len(cosine_calls), 32)

    def test_signature_shortlist_finds_nonlexical_semantic_target(self):
        self._insert_chunks(500, semantic_target_index=251)
        original_cached = rag_core.cached_rag_embedding
        original_limit = rag_core.rag_semantic_candidate_limit
        rag_core.cached_rag_embedding = lambda _db, _query, *, app_settings=None: [1.0, 0.0]
        rag_core.rag_semantic_candidate_limit = lambda *, app_settings=None: 16
        try:
            results = _m_rag.retrieve_data_bank(
                self.db, "chat", "meaningfulconcept", limit=3, app_settings=self.app_settings_builder.build()
            )
        finally:
            rag_core.cached_rag_embedding = original_cached
            rag_core.rag_semantic_candidate_limit = original_limit

        self.assertTrue(results)
        self.assertEqual(results[0][1], "chunk 251")

    def test_add_document_embedding_batches_run_outside_write_transaction(self):
        original_extract = rag_core.extract_data_bank_text
        original_split = rag_core.split_data_bank_chunks
        original_embed = rag_core.embed_rag_batch
        transaction_states = []

        rag_core.extract_data_bank_text = lambda _filename, _raw, *, app_settings=None: "content"
        rag_core.split_data_bank_chunks = lambda _text: [f"chunk {index}" for index in range(65)]

        def fake_embed(texts, *, app_settings=None):
            transaction_states.append(self.db.in_transaction)
            return [[1.0, 0.0] for _ in texts]

        rag_core.embed_rag_batch = fake_embed
        try:
            status, count = _m_telegram.add_data_bank_document(
                self.db, "chat", "batched.txt", b"batched-payload", app_settings=self.app_settings_builder.build()
            )
        finally:
            rag_core.extract_data_bank_text = original_extract
            rag_core.split_data_bank_chunks = original_split
            rag_core.embed_rag_batch = original_embed

        self.assertEqual((status, count), ("added", 65))
        self.assertEqual(transaction_states, [False, False, False])
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM data_bank_embeddings").fetchone()[0],
            65,
        )

        rows = self.db.execute(
            "SELECT embedding_namespace,vector_signature,vector_norm FROM data_bank_embeddings"
        ).fetchall()
        self.assertEqual(len(rows), 65)
        self.assertTrue(
            all(
                namespace == self.namespace and signature is not None and norm is not None
                for namespace, signature, norm in rows
            )
        )

    def test_reindex_embedding_batches_release_write_transaction_between_calls(self):
        now = time.time()
        self.db.execute(
            "INSERT INTO data_bank_documents("
            "chat_id,document_id,filename,byte_size,chunk_count,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?)",
            ("chat", "reindex-doc", "reindex.txt", 65, 65, now, now),
        )
        for index in range(65):
            self.db.execute(
                "INSERT INTO data_bank_chunks(chat_id,document_id,chunk_index,content) VALUES(?,?,?,?)",
                ("chat", "reindex-doc", index, f"chunk {index}"),
            )
        self.db.commit()

        original_embed = rag_core.embed_rag_batch
        transaction_states = []

        def fake_embed(texts, *, app_settings=None):
            transaction_states.append(self.db.in_transaction)
            return [[1.0, 0.0] for _ in texts]

        rag_core.embed_rag_batch = fake_embed
        try:
            total, indexed = _m_help.reindex_data_bank_documents(
                self.db, "chat", "reindex.txt", app_settings=self.app_settings_builder.build()
            )
        finally:
            rag_core.embed_rag_batch = original_embed

        self.assertEqual((total, indexed), (65, 65))
        self.assertEqual(transaction_states, [False, False, False])
        self.assertFalse(self.db.in_transaction)

        rows = self.db.execute(
            "SELECT embedding_namespace,vector_signature,vector_norm FROM data_bank_embeddings"
        ).fetchall()
        self.assertEqual(len(rows), 65)
        self.assertTrue(
            all(
                namespace == self.namespace and signature is not None and norm is not None
                for namespace, signature, norm in rows
            )
        )

    def test_rag_embedding_schema_is_canonical_without_legacy_backfill(self):
        columns = {row[1]: row for row in self.db.execute("PRAGMA table_info(data_bank_embeddings)").fetchall()}
        self.assertEqual(columns["embedding_namespace"][3], 1)
        self.assertIsNone(columns["embedding_namespace"][4])
        self.assertEqual(columns["vector_signature"][3], 1)
        self.assertIsNone(columns["vector_signature"][4])
        self.assertEqual(columns["vector_norm"][3], 1)
        self.assertIsNone(columns["vector_norm"][4])

        cache_columns = {row[1]: row for row in self.db.execute("PRAGMA table_info(rag_embedding_cache)").fetchall()}
        self.assertEqual(cache_columns["vector_norm"][3], 1)
        self.assertIsNone(cache_columns["vector_norm"][4])
        self.assertFalse(hasattr(rag_core, "backfill_rag_embedding_signatures"))


if __name__ == "__main__":
    unittest.main()
