import threading
import time
import unittest

from settings_test_support import SettingsTestCase

from bridge.persona_integrity import IntegrityCheckedPersonaStore


class IntegrityCheckedPersonaStoreTests(SettingsTestCase):
    def setUp(self):
        self.personas = {
            "native.png": {
                "name": "Native",
                "description": "Native description",
                "sillytavern_avatar": "native.png",
            }
        }
        self.lock = threading.RLock()
        self.upserts = []
        self.deletes = []

        def load_personas():
            return dict(self.personas)

        def upsert(identifier, name, description, client=None):
            self.upserts.append((identifier, name, description, client))
            return str(identifier)

        def delete(identifier, client=None):
            self.deletes.append((identifier, client))
            return True

        self.store = IntegrityCheckedPersonaStore(
            load_personas=load_personas,
            upsert_backend=upsert,
            delete_backend=delete,
            valid_avatar=lambda value: (
                str(value) if str(value).endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")) else ""
            ),
            edit_lock=lambda: self.lock,
        )

    def test_rejects_existing_bridge_logical_id_by_avatar_stem(self):
        self.personas["bridge-writer.webp"] = {
            "name": "Existing",
            "description": "Existing",
            "sillytavern_avatar": "bridge-writer.webp",
        }

        with self.assertRaisesRegex(
            ValueError,
            "Persona ID already exists",
        ):
            self.store.upsert(
                "writer",
                "Writer",
                "Description",
            )

        self.assertEqual(self.upserts, [])

    def test_explicit_native_avatar_update_bypasses_logical_id_collision_check(self):
        result = self.store.upsert(
            "native.png",
            "Updated",
            "Updated description",
            client="client",
        )

        self.assertEqual(result, "native.png")
        self.assertEqual(
            self.upserts,
            [
                (
                    "native.png",
                    "Updated",
                    "Updated description",
                    "client",
                )
            ],
        )

    def test_delete_delegates_and_preserves_client(self):
        self.assertTrue(
            self.store.delete(
                "native.png",
                client="client",
            )
        )
        self.assertEqual(
            self.deletes,
            [("native.png", "client")],
        )

    def test_backend_exceptions_propagate_unchanged(self):
        original = RuntimeError("offline")
        store = IntegrityCheckedPersonaStore(
            load_personas=lambda force=False: {},
            upsert_backend=lambda *_args, **_kwargs: (_ for _ in ()).throw(original),
            delete_backend=lambda *_args, **_kwargs: True,
            valid_avatar=lambda _value: "",
            edit_lock=lambda: self.lock,
        )

        with self.assertRaises(RuntimeError) as caught:
            store.upsert("writer", "Writer", "Description")

        self.assertIs(caught.exception, original)

    def _maximum_backend_parallelism(self, operations):
        active = 0
        maximum = 0
        counter_lock = threading.Lock()
        start = threading.Barrier(len(operations) + 1)
        errors = []

        def backend_enter():
            nonlocal active, maximum
            with counter_lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with counter_lock:
                active -= 1

        store = IntegrityCheckedPersonaStore(
            load_personas=lambda force=False: {},
            upsert_backend=lambda *_args, **_kwargs: backend_enter() or "avatar.png",
            delete_backend=lambda *_args, **_kwargs: backend_enter() or True,
            valid_avatar=lambda value: str(value) if str(value).endswith(".png") else "",
            edit_lock=lambda: self.lock,
        )

        def run(operation):
            try:
                start.wait(timeout=2)
                operation(store)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(operation,)) for operation in operations]
        for thread in threads:
            thread.start()
        start.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=2)

        self.assertEqual(errors, [])
        return maximum

    def test_two_upserts_are_serialized(self):
        maximum = self._maximum_backend_parallelism(
            [
                lambda store: store.upsert(
                    "one",
                    "One",
                    "Description",
                ),
                lambda store: store.upsert(
                    "two",
                    "Two",
                    "Description",
                ),
            ]
        )
        self.assertEqual(maximum, 1)

    def test_upsert_and_delete_are_serialized(self):
        maximum = self._maximum_backend_parallelism(
            [
                lambda store: store.upsert(
                    "writer",
                    "Writer",
                    "Description",
                ),
                lambda store: store.delete("native.png"),
            ]
        )
        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
