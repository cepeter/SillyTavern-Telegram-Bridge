from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class RagScalingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = rt.DB_FILE
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.namespace = rt.rag_embedding_namespace()

    def tearDown(self):
        self.db.close()
        rt.DB_FILE = self.original_db
        self.tmp.cleanup()

    def _insert_chunks(
        self,
        count: int,
        needle_index: int | None = None,
        semantic_target_index: int | None = None,
        with_signatures: bool = True,
    ):
        now = rt.time.time()
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
            if with_signatures:
                self.db.execute(
                    "INSERT INTO data_bank_embeddings(chunk_id,embedding_namespace,dimensions,vector_json,vector_signature) "
                    "VALUES(?,?,?,?,?)",
                    (
                        chunk_id,
                        self.namespace,
                        2,
                        rt.json.dumps(vector),
                        rt.embedding_signature(vector),
                    ),
                )
            else:
                self.db.execute(
                    "INSERT INTO data_bank_embeddings(chunk_id,embedding_namespace,dimensions,vector_json) VALUES(?,?,?,?)",
                    (chunk_id, self.namespace, 2, rt.json.dumps(vector)),
                )
            self.db.execute(
                "INSERT INTO data_bank_fts(content,chat_id,document_id,filename,chunk_id) VALUES(?,?,?,?,?)",
                (content, "chat", document_id, "large.txt", chunk_id),
            )
        self.db.commit()
        return ids

    def test_small_corpus_keeps_exact_semantic_candidate_set(self):
        ids = self._insert_chunks(20)
        candidates = rt.semantic_candidate_chunk_ids(
            self.db, "chat", self.namespace, [], candidate_limit=64
        )
        self.assertEqual(candidates, tuple(ids))

    def test_large_corpus_is_bounded_and_keeps_lexical_neighborhood(self):
        ids = self._insert_chunks(500)
        hit = ids[250]
        candidates = rt.semantic_candidate_chunk_ids(
            self.db, "chat", self.namespace, [hit], candidate_limit=64
        )
        self.assertLessEqual(len(candidates), 64)
        for expected in ids[248:253]:
            self.assertIn(expected, candidates)

    def test_retrieve_decodes_only_bounded_semantic_shortlist(self):
        self._insert_chunks(300, needle_index=150)
        original_cached = rt.cached_rag_embedding
        original_limit = rt.rag_semantic_candidate_limit
        original_cosine = rt.cosine_similarity
        cosine_calls = []
        rt.cached_rag_embedding = lambda _db, _query: [1.0, 0.0]
        rt.rag_semantic_candidate_limit = lambda: 32

        def counted_cosine(left, right):
            cosine_calls.append((left, right))
            return original_cosine(left, right)

        rt.cosine_similarity = counted_cosine
        try:
            results = rt.retrieve_data_bank(self.db, "chat", "needle", limit=5)
        finally:
            rt.cached_rag_embedding = original_cached
            rt.rag_semantic_candidate_limit = original_limit
            rt.cosine_similarity = original_cosine

        self.assertTrue(results)
        self.assertGreater(len(cosine_calls), 0)
        self.assertLessEqual(len(cosine_calls), 32)


    def test_signature_shortlist_finds_nonlexical_semantic_target(self):
        self._insert_chunks(500, semantic_target_index=251)
        original_cached = rt.cached_rag_embedding
        original_limit = rt.rag_semantic_candidate_limit
        rt.cached_rag_embedding = lambda _db, _query: [1.0, 0.0]
        rt.rag_semantic_candidate_limit = lambda: 16
        try:
            results = rt.retrieve_data_bank(
                self.db,
                "chat",
                "meaningfulconcept",
                limit=3,
            )
        finally:
            rt.cached_rag_embedding = original_cached
            rt.rag_semantic_candidate_limit = original_limit

        self.assertTrue(results)
        self.assertEqual(results[0][1], "chunk 251")

    def test_legacy_embedding_signatures_are_backfilled_lazily(self):
        self._insert_chunks(30, with_signatures=False)
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM data_bank_embeddings WHERE vector_signature IS NOT NULL"
            ).fetchone()[0],
            0,
        )
        updated = rt.backfill_rag_embedding_signatures(
            self.db,
            "chat",
            self.namespace,
            limit=10,
        )
        self.assertEqual(updated, 10)
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM data_bank_embeddings WHERE vector_signature IS NOT NULL"
            ).fetchone()[0],
            10,
        )


if __name__ == "__main__":
    unittest.main()
