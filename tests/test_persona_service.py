import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge.runtime as rt
from bridge.persona_service import PersonaService


class PersonaServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.personas = {
            "native.png": {
                "name": "Native",
                "description": "Original",
                "sillytavern_avatar": "native.png",
            }
        }
        self.session_updates = []
        self.upserts = []
        self.deletes = []
        self.references = 0
        self.service = PersonaService(
            load_personas=lambda: dict(self.personas),
            load_default_persona=lambda: "native.png",
            upsert_persona=self._upsert,
            delete_persona=self._delete,
            update_session_persona=self._update_session,
            persona_reference_count=lambda _db, _persona_id: self.references,
        )

    def tearDown(self):
        self.db.close()

    def _upsert(self, persona_id, name, description):
        self.upserts.append((persona_id, name, description))
        avatar = (
            persona_id
            if persona_id.endswith(".png")
            else f"bridge-{persona_id}.png"
        )
        self.personas[avatar] = {
            "name": name,
            "description": description,
            "sillytavern_avatar": avatar,
        }
        return avatar

    def _delete(self, persona_id):
        self.deletes.append(persona_id)
        return self.personas.pop(persona_id, None) is not None

    def _update_session(self, db, chat_id, session_id, **kwargs):
        self.session_updates.append((db, chat_id, session_id, kwargs))

    def test_reads_delegate_without_mutation(self):
        listing = self.service.list()
        self.assertEqual(set(listing), {"native.png"})
        self.assertIsNot(listing, self.personas)
        self.assertEqual(self.service.get("native.png")["name"], "Native")
        self.assertEqual(self.service.name("native.png"), "Native")
        self.assertEqual(self.service.name("missing"), "")
        self.assertEqual(self.service.default_id(), "native.png")
        self.assertEqual(self.session_updates, [])
        self.assertEqual(self.upserts, [])
        self.assertEqual(self.deletes, [])

    def test_create_and_select_persists_then_selects_native_avatar(self):
        avatar = self.service.create_and_select(
            self.db,
            "chat",
            "session",
            "writer",
            "Writer",
            "Writes notes",
            operation_id=42,
        )
        self.assertEqual(avatar, "bridge-writer.png")
        self.assertEqual(
            self.upserts,
            [("writer", "Writer", "Writes notes")],
        )
        self.assertEqual(
            self.session_updates[-1][3],
            {
                "persona_id": "bridge-writer.png",
                "operation_id": 42,
                "operation_kind": "persona_create",
            },
        )

    def test_invalid_create_never_calls_store_or_session(self):
        with self.assertRaises(ValueError):
            self.service.create_and_select(
                self.db,
                "chat",
                "session",
                "bad id!",
                "Writer",
                "Description",
            )
        self.assertEqual(self.upserts, [])
        self.assertEqual(self.session_updates, [])

    def test_create_rejects_duplicate_bridge_logical_id(self):
        self.personas["bridge-writer.png"] = {
            "name": "Existing",
            "description": "Existing",
            "sillytavern_avatar": "bridge-writer.png",
        }
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.service.create_and_select(
                self.db,
                "chat",
                "session",
                "writer",
                "Writer",
                "Description",
            )
        self.assertEqual(self.upserts, [])
        self.assertEqual(self.session_updates, [])

    def test_create_validates_name_and_description_bounds(self):
        cases = (
            ("", "Description"),
            ("x" * 121, "Description"),
            ("Writer", ""),
            ("Writer", "x" * 4001),
        )
        for name, description in cases:
            with self.subTest(name_length=len(name), description_length=len(description)):
                with self.assertRaises(ValueError):
                    self.service.create_and_select(
                        self.db,
                        "chat",
                        "session",
                        "writer",
                        name,
                        description,
                    )
        self.assertEqual(self.upserts, [])
        self.assertEqual(self.session_updates, [])

    def test_update_existing_persona_delegates_to_store(self):
        avatar = self.service.update(
            "native.png",
            "Updated",
            "Updated description",
        )
        self.assertEqual(avatar, "native.png")
        self.assertEqual(
            self.upserts,
            [("native.png", "Updated", "Updated description")],
        )

    def test_update_requires_existing_persona(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            self.service.update(
                "missing.png",
                "Missing",
                "Description",
            )
        self.assertEqual(self.upserts, [])

    def test_select_existing_persona_updates_session(self):
        self.assertTrue(
            self.service.select(
                self.db,
                "chat",
                "session",
                "native.png",
                operation_id=7,
            )
        )
        self.assertEqual(
            self.session_updates[-1][3],
            {
                "persona_id": "native.png",
                "operation_id": 7,
                "operation_kind": "persona_select",
            },
        )

    def test_select_missing_returns_false_without_mutation(self):
        self.assertFalse(
            self.service.select(
                self.db,
                "chat",
                "session",
                "missing.png",
            )
        )
        self.assertEqual(self.session_updates, [])

    def test_disable_clears_session_persona(self):
        self.service.disable(
            self.db,
            "chat",
            "session",
            operation_id=9,
        )
        self.assertEqual(
            self.session_updates[-1][3],
            {
                "persona_id": "",
                "operation_id": 9,
                "operation_kind": "persona_select",
            },
        )

    def test_delete_unused_persona_delegates_to_store(self):
        self.assertTrue(
            self.service.delete_if_unused(self.db, "native.png")
        )
        self.assertEqual(self.deletes, ["native.png"])

    def test_delete_refuses_referenced_persona(self):
        self.references = 1
        with self.assertRaisesRegex(
            ValueError,
            "used by another session",
        ):
            self.service.delete_if_unused(self.db, "native.png")
        self.assertEqual(self.deletes, [])

    def test_create_does_not_delete_native_persona_when_session_select_fails(self):
        def failing_update(*_args, **_kwargs):
            raise RuntimeError("session write failed")

        service = PersonaService(
            load_personas=lambda: dict(self.personas),
            load_default_persona=lambda: "native.png",
            upsert_persona=self._upsert,
            delete_persona=self._delete,
            update_session_persona=failing_update,
            persona_reference_count=lambda _db, _persona_id: 0,
        )
        with self.assertRaisesRegex(RuntimeError, "session write failed"):
            service.create_and_select(
                self.db,
                "chat",
                "session",
                "writer",
                "Writer",
                "Writes notes",
            )
        self.assertIn("bridge-writer.png", self.personas)
        self.assertEqual(self.deletes, [])

    def test_delete_checks_current_reference_count_on_each_call(self):
        self.references = 1
        with self.assertRaisesRegex(
            ValueError,
            "used by another session",
        ):
            self.service.delete_if_unused(self.db, "native.png")
        self.references = 0
        self.assertTrue(
            self.service.delete_if_unused(self.db, "native.png")
        )
        self.assertEqual(self.deletes, ["native.png"])


class PersonaSourceBoundaryTests(unittest.TestCase):
    def _function_chunk(self, source, marker):
        start = source.index(marker)
        next_def = source.find("\ndef ", start + len(marker))
        return source[start: next_def if next_def >= 0 else None]

    def test_migrated_persona_application_paths_do_not_call_raw_lifecycle(self):
        root = Path(__file__).parents[1] / "bridge"
        input_source = (root / "input_flows.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "upsert_native_persona(",
            "delete_native_persona(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, input_source)

        for marker in (
            "def _handle_persona_input",
            "def handle_persona_callback",
        ):
            chunk = self._function_chunk(input_source, marker)
            self.assertNotIn("update_session(", chunk, marker)

    def test_persona_menu_reads_only_through_service_boundary(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "cards.py"
        ).read_text(encoding="utf-8")
        chunk = self._function_chunk(source, "def send_persona_menu")
        self.assertNotIn("load_personas(", chunk)
        self.assertNotIn("persona_name(", chunk)


class PersonaCompatibilityServiceTests(unittest.TestCase):
    def test_compatibility_service_late_binds_final_runtime_collaborators(self):
        calls = []

        with patch.object(
            rt,
            "load_personas",
            return_value={
                "native.png": {
                    "name": "Native",
                    "description": "D",
                    "sillytavern_avatar": "native.png",
                }
            },
        ), patch.object(
            rt,
            "default_persona_id",
            return_value="native.png",
        ), patch.object(
            rt,
            "upsert_native_persona",
            side_effect=lambda *args: calls.append(("upsert", args))
            or "native.png",
        ), patch.object(
            rt,
            "delete_native_persona",
            side_effect=lambda persona_id: calls.append(
                ("delete", persona_id)
            )
            or True,
        ), patch.object(
            rt,
            "_repo_count_persona_references",
            return_value=0,
            create=True,
        ):
            service = rt.compatibility_persona_service()
            self.assertEqual(service.name("native.png"), "Native")
            self.assertEqual(service.default_id(), "native.png")
            service.update("native.png", "Updated", "D")

        self.assertEqual(
            calls,
            [("upsert", ("native.png", "Updated", "D"))],
        )

    def test_resolve_persona_service_prefers_injected_service(self):
        sentinel = object()
        self.assertIs(rt.resolve_persona_service(sentinel), sentinel)


if __name__ == "__main__":
    unittest.main()
