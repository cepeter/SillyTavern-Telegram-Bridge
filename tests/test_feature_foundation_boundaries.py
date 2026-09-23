"""Phase 7B3 memory/group feature-foundation import-boundary tests."""

from pathlib import Path
import subprocess
import sys
import unittest


REPO_ROOT = Path(__file__).parents[1]

NETWORK_EXPORTS = (
    "validate_provider_endpoint",
    "strict_urlopen",
)

MEMORY_BACKEND_EXPORTS = (
    "hindsight_bank_id",
    "hindsight_tags",
    "hindsight_client",
    "close_hindsight_client",
    "hindsight_session_lock",
    "hindsight_session_prefix",
    "hindsight_conversation_document_id",
    "hindsight_explicit_document_id",
    "memory_mode",
    "memory_scope",
    "memory_recall_filter",
    "recall_memory_results",
    "recall_memory_context",
    "remember_fact",
)

RAG_CORE_EXPORTS = (
    "extract_pdf_data_bank_text",
    "extract_data_bank_text",
    "split_data_bank_chunks",
    "rag_embedding_namespace",
    "embedding_norm",
    "rag_embedding_headers",
    "embed_rag_text",
    "embed_rag_batch",
    "rag_semantic_candidate_limit",
    "add_data_bank_document",
    "cached_rag_embedding",
    "retrieve_data_bank",
    "rag_mode",
    "rag_retrieval_bundle",
    "rag_context_for_prompt",
    "rag_citation_footer",
    "data_bank_documents",
    "data_bank_document_versions",
    "activate_data_bank_version",
    "delete_data_bank_documents",
    "rag_embedding_coverage",
    "reindex_data_bank_documents",
)

GROUP_CORE_EXPORTS = (
    "group_state",
    "group_user_turn_allowed",
    "claim_group_user_turn",
    "pass_group_user_turn",
    "group_setup_state",
    "group_character_option_label",
    "save_group_state",
    "resolve_character_file",
    "group_member_labels",
    "group_current_speaker",
    "advance_group_turn",
)


class Phase7B3FeatureFoundationsTests(unittest.TestCase):
    def _run_python(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_config_owns_memory_and_rag_defaults(self):
        import bridge.config as config

        self.assertEqual(config.HINDSIGHT_DEFAULT_URL, "http://127.0.0.1:8890")
        self.assertEqual(config.HINDSIGHT_RECALL_MAX_TOKENS, 1200)
        self.assertEqual(config.HINDSIGHT_CONTEXT_MAX_CHARS, 6000)
        self.assertEqual(config.HINDSIGHT_RETAIN_MAX_MESSAGES, 100)
        self.assertEqual(config.SUMMARY_TRIGGER_MESSAGES, 32)
        self.assertEqual(config.SUMMARY_RECENT_MESSAGES, 24)
        self.assertEqual(config.SUMMARY_MAX_CHARS, 12000)
        self.assertEqual(config.SUMMARY_MAX_OUTPUT_TOKENS, 1200)
        self.assertEqual(config.SUMMARY_UPDATE_INTERVAL, 8)
        self.assertEqual(config.RAG_MAX_FILE_BYTES, 10 * 1024 * 1024)
        self.assertEqual(config.RAG_CHUNK_CHARS, 1800)
        self.assertEqual(config.RAG_CHUNK_OVERLAP, 220)
        self.assertEqual(config.RAG_MAX_CONTEXT_CHARS, 6000)
        self.assertEqual(config.RAG_EMBEDDING_MODEL, "text-embedding-3-small")

    def test_common_no_longer_defines_memory_or_rag_defaults(self):
        source = (REPO_ROOT / "bridge" / "common.py").read_text(encoding="utf-8")
        prefixes = (
            "HINDSIGHT_DEFAULT_URL = ",
            "HINDSIGHT_RECALL_MAX_TOKENS = ",
            "HINDSIGHT_CONTEXT_MAX_CHARS = ",
            "HINDSIGHT_RETAIN_MAX_MESSAGES = ",
            "SUMMARY_TRIGGER_MESSAGES = ",
            "SUMMARY_RECENT_MESSAGES = ",
            "SUMMARY_MAX_CHARS = ",
            "SUMMARY_MAX_OUTPUT_TOKENS = ",
            "SUMMARY_UPDATE_INTERVAL = ",
            "RAG_MAX_FILE_BYTES = ",
            "RAG_CHUNK_CHARS = ",
            "RAG_CHUNK_OVERLAP = ",
            "RAG_MAX_CONTEXT_CHARS = ",
            "RAG_SUPPORTED_SUFFIXES = ",
            "RAG_EMBEDDING_URL = ",
            "RAG_EMBEDDING_MODEL = ",
            "RAG_EMBEDDING_DIMENSIONS = ",
            "RAG_MAX_EXTRACTED_CHARS = ",
            "RAG_MAX_PDF_PAGES = ",
            "RAG_PDF_PARSE_TIMEOUT_SECONDS = ",
        )
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                self.assertNotIn("\n" + prefix, source)

    def test_new_foundations_import_without_shared_runtime(self):
        modules = (
            "bridge.network_security",
            "bridge.memory_backend",
            "bridge.rag_core",
            "bridge.group_core",
        )
        for module in modules:
            with self.subTest(module=module):
                completed = self._run_python(
                    "import sys\n"
                    f"import {module}\n"
                    "assert 'bridge.runtime' not in sys.modules\n"
                    "assert 'bridge.common' not in sys.modules\n"
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + completed.stderr,
                )

    def test_network_security_is_canonical_owner(self):
        transport = (REPO_ROOT / "bridge" / "provider_transport.py").read_text(
            encoding="utf-8"
        )
        generation = (REPO_ROOT / "bridge" / "generation.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("def validate_provider_endpoint(", transport)
        self.assertNotIn("def strict_urlopen(", transport)
        self.assertIn("from bridge.network_security import", transport)
        self.assertNotIn("from bridge.network_security import", generation)

    def test_memory_backend_has_no_ui_or_generation_dependency(self):
        source = (REPO_ROOT / "bridge" / "memory_backend.py").read_text(encoding="utf-8")
        for forbidden in (
            "bridge.runtime",
            "bridge.common",
            "bridge.telegram",
            "bridge.generation",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_memory_backend_is_single_hindsight_lock_owner(self):
        backend = (REPO_ROOT / "bridge" / "memory_backend.py").read_text(encoding="utf-8")
        shell = (REPO_ROOT / "bridge" / "memory.py").read_text(encoding="utf-8")
        self.assertEqual(backend.count("_HINDSIGHT_SESSION_LOCKS:"), 1)
        self.assertEqual(
            backend.count("_HINDSIGHT_SESSION_LOCKS_GUARD ="),
            1,
        )
        self.assertNotIn("_HINDSIGHT_SESSION_LOCKS:", shell)
        self.assertNotIn("_HINDSIGHT_SESSION_LOCKS_GUARD =", shell)

    def test_memory_shell_does_not_redefine_backend_public_api(self):
        source = (REPO_ROOT / "bridge" / "memory.py").read_text(encoding="utf-8")
        for name in MEMORY_BACKEND_EXPORTS:
            with self.subTest(name=name):
                self.assertNotIn(f"def {name}(", source)
                self.assertNotIn(f"async def {name}(", source)

    def test_rag_core_has_no_ui_or_shared_runtime_dependency(self):
        source = (REPO_ROOT / "bridge" / "rag_core.py").read_text(encoding="utf-8")
        for forbidden in (
            "bridge.runtime",
            "bridge.common",
            "bridge.telegram",
            "send_text(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_rag_shell_does_not_redefine_core_api(self):
        source = (REPO_ROOT / "bridge" / "rag.py").read_text(encoding="utf-8")
        for name in RAG_CORE_EXPORTS:
            with self.subTest(name=name):
                self.assertNotIn(f"def {name}(", source)

    def test_group_core_has_no_ui_generation_or_shared_runtime_dependency(self):
        source = (REPO_ROOT / "bridge" / "group_core.py").read_text(encoding="utf-8")
        for forbidden in (
            "bridge.runtime",
            "bridge.common",
            "bridge.telegram",
            "bridge.generation",
            "send_text(",
            "send_panel_message(",
            "generate_text(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_group_shell_does_not_redefine_core_api(self):
        source = (REPO_ROOT / "bridge" / "groups.py").read_text(encoding="utf-8")
        for name in GROUP_CORE_EXPORTS:
            with self.subTest(name=name):
                self.assertNotIn(f"def {name}(", source)

    def test_canonical_foundation_exports_are_directly_available(self):
        import bridge.group_core as group_core
        import bridge.memory_backend as memory_backend
        import bridge.network_security as network_security
        import bridge.rag_core as rag_core

        for module, names in (
            (network_security, NETWORK_EXPORTS),
            (memory_backend, MEMORY_BACKEND_EXPORTS),
            (rag_core, RAG_CORE_EXPORTS),
            (group_core, GROUP_CORE_EXPORTS),
        ):
            for name in names:
                with self.subTest(module=module.__name__, name=name):
                    self.assertTrue(hasattr(module, name))

    def test_phase_7b3_foundations_remain_ordinary_after_final_cutover(self):
        self.assertFalse((REPO_ROOT / "bridge" / "runtime_loader.py").exists())


    def test_feature_shells_are_ordinary_after_final_cutover(self):
        for module_name in ("bridge.memory", "bridge.rag", "bridge.groups"):
            with self.subTest(module=module_name):
                __import__(module_name)


if __name__ == "__main__":
    unittest.main()
