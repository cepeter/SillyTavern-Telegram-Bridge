from application_test_setup import ensure_application_extensions, make_test_provider_port

ensure_application_extensions()

import base64
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from settings_test_support import SettingsTestCase

import bridge.character_quality as quality
from bridge.card_content import parse_png_chara_bytes
from bridge.memory_curator import db_connect


def _minimal_png(card: dict) -> bytes:
    """Build a parseable PNG: signature plus one SillyTavern chara tEXt chunk."""
    encoded = base64.b64encode(json.dumps(card).encode("utf-8"))
    chunk_data = b"chara\x00" + encoded
    chunk = (
        struct.pack(">I", len(chunk_data))
        + b"tEXt"
        + chunk_data
        + struct.pack(">I", zlib.crc32(b"tEXt" + chunk_data) & 0xFFFFFFFF)
    )
    return b"\x89PNG\r\n\x1a\n" + chunk


class RankBadgeTests(unittest.TestCase):
    def test_badges_map_tiers(self):
        self.assertEqual(quality.rank_badge("S"), "🏆S ")
        self.assertEqual(quality.rank_badge("a"), "🥇A ")
        self.assertEqual(quality.rank_badge("D"), "⚪D ")

    def test_unknown_or_empty_returns_empty(self):
        self.assertEqual(quality.rank_badge(None), "")
        self.assertEqual(quality.rank_badge(""), "")
        self.assertEqual(quality.rank_badge("Z"), "")


class ParseRankTests(unittest.TestCase):
    def test_single_letter(self):
        self.assertEqual(quality.parse_rank("S"), "S")

    def test_letter_with_justification(self):
        self.assertEqual(quality.parse_rank("A — the personality is sharp"), "A")

    def test_prefixed_label(self):
        self.assertEqual(quality.parse_rank("Rank: B"), "B")

    def test_garbage_returns_none(self):
        self.assertIsNone(quality.parse_rank(""))
        self.assertIsNone(quality.parse_rank("this is not a rank"))


class ParseOptimizedFieldsTests(unittest.TestCase):
    def test_bare_json(self):
        raw = '{"description": "new desc", "personality": "new persona", "bogus": 1}'
        result = quality.parse_optimized_fields(raw)
        self.assertEqual(result, {"description": "new desc", "personality": "new persona"})

    def test_fenced_json(self):
        raw = '```json\n{"description": "new"}\n```'
        self.assertEqual(quality.parse_optimized_fields(raw), {"description": "new"})

    def test_invalid_returns_none(self):
        self.assertIsNone(quality.parse_optimized_fields(""))
        self.assertIsNone(quality.parse_optimized_fields("no json here"))
        self.assertIsNone(quality.parse_optimized_fields('{"unrelated": "value"}'))


class MergeOptimizedFieldsTests(unittest.TestCase):
    def test_top_level_container(self):
        card = {"name": "Alice", "description": "old"}
        result = quality.merge_optimized_fields(card, {"description": "new"})
        self.assertEqual(result["description"], "new")
        self.assertEqual(card["description"], "old")
        self.assertEqual(card["name"], "Alice")

    def test_data_container(self):
        card = {"data": {"name": "Alice", "description": "old"}, "spec": "chara_card_v2"}
        result = quality.merge_optimized_fields(card, {"description": "new"})
        self.assertEqual(result["data"]["description"], "new")
        self.assertEqual(card["data"]["description"], "old")
        self.assertEqual(card["data"]["name"], "Alice")


class WritePngCharaBytesTests(unittest.TestCase):
    def test_roundtrip_preserves_other_chunks(self):
        card = {"name": "Alice", "description": "old", "personality": "p"}
        raw = _minimal_png(card)
        rewritten = quality.write_png_chara_bytes(raw, {**card, "description": "new"})
        parsed = parse_png_chara_bytes(rewritten)
        self.assertEqual(parsed["description"], "new")
        self.assertEqual(parsed["name"], "Alice")

    def test_rejects_non_png(self):
        with self.assertRaises(ValueError):
            quality.write_png_chara_bytes(b"not a png", {})

    def test_rejects_missing_chara_chunk(self):
        fake = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 0) + b"IEND" + struct.pack(">I", 0)
        with self.assertRaises(ValueError):
            quality.write_png_chara_bytes(fake, {})


class CharacterQualityPersistenceTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.app_settings_builder.character_dir = Path(self.tmp.name) / "characters"
        self.app_settings = self.app_settings_builder.build()
        self.app_settings.character_dir.mkdir()
        for name in ("alice", "bob", "carol"):
            (self.app_settings.character_dir / f"{name}.png").write_bytes(_minimal_png({"name": name}))
        self.db = db_connect(app_settings=self.app_settings)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_store_and_load_rank(self):
        quality.store_character_rank(self.db, "alice.png", "S", app_settings=self.app_settings)
        self.assertEqual(quality.character_rank(self.db, "alice.png", app_settings=self.app_settings), "S")

    def test_invalid_tier_not_stored(self):
        quality.store_character_rank(self.db, "bob.png", "Z", app_settings=self.app_settings)
        self.assertEqual(quality.character_rank(self.db, "bob.png", app_settings=self.app_settings), "")

    def test_unranked_defaults_empty(self):
        self.assertEqual(quality.character_rank(self.db, "carol.png", app_settings=self.app_settings), "")


class CharacterQualityModelTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.app_settings_builder.character_dir = Path(self.tmp.name) / "characters"
        self.app_settings = self.app_settings_builder.build()
        self.app_settings.character_dir.mkdir()
        for name in ("alice", "bob", "carol"):
            (self.app_settings.character_dir / f"{name}.png").write_bytes(_minimal_png({"name": name}))
        self.db = db_connect(app_settings=self.app_settings)
        self.session = {"session_id": "s", "model_id": "m"}

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_rank_character_stores_parsed_tier(self):
        port = make_test_provider_port(generate_backend=lambda *a, **k: "S — distinctive and engaging")
        with mock.patch.object(quality, "task_model_for_session", return_value="test-model"):
            rank = quality.rank_character(
                self.db,
                "chat",
                self.session,
                {"name": "Alice", "description": "x"},
                "alice.png",
                provider_port=port,
                app_settings=self.app_settings,
            )
        self.assertEqual(rank, "S")
        self.assertEqual(quality.character_rank(self.db, "alice.png", app_settings=self.app_settings), "S")

    def test_rank_character_returns_none_on_provider_failure(self):
        def boom(*a, **k):
            raise RuntimeError("provider down")

        port = make_test_provider_port(generate_backend=boom)
        with mock.patch.object(quality, "task_model_for_session", return_value="test-model"):
            rank = quality.rank_character(
                self.db,
                "chat",
                self.session,
                {"name": "Alice"},
                "alice.png",
                provider_port=port,
                app_settings=self.app_settings,
            )
        self.assertIsNone(rank)
        self.assertEqual(quality.character_rank(self.db, "alice.png", app_settings=self.app_settings), "")

    def test_optimize_character_parses_json_reply(self):
        def backend(*a, **k):
            return json.dumps({"description": "new", "personality": "refined"})

        port = make_test_provider_port(generate_backend=backend)
        with mock.patch.object(quality, "task_model_for_session", return_value="test-model"):
            result = quality.optimize_character(
                self.db,
                "chat",
                self.session,
                {"name": "Alice", "description": "old"},
                provider_port=port,
                app_settings=self.app_settings,
            )
        self.assertEqual(result, {"description": "new", "personality": "refined"})

    def test_optimize_character_returns_none_on_invalid_output(self):
        port = make_test_provider_port(generate_backend=lambda *a, **k: "no json here")
        with mock.patch.object(quality, "task_model_for_session", return_value="test-model"):
            result = quality.optimize_character(
                self.db,
                "chat",
                self.session,
                {"name": "Alice"},
                provider_port=port,
                app_settings=self.app_settings,
            )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()


class OptimizerSuggestionPromptTests(unittest.TestCase):
    def test_optimizer_prompt_includes_manual_suggestion_as_bounded_guidance(self):
        prompt = quality.optimize_prompt(
            {"name": "Alice", "description": "old"},
            suggestion="Make her more sarcastic, preserve the backstory.",
        )
        joined = "\n".join(str(message["content"]) for message in prompt)
        self.assertIn("<user_suggestion>", joined)
        self.assertIn("Make her more sarcastic, preserve the backstory.", joined)
        self.assertIn("does not override", joined)
