import sqlite3
import threading
import time
import unittest

from bridge.hindsight_integrity import HindsightStaleGuard


class HindsightStaleGuardTests(unittest.TestCase):
    def setUp(self):
        self.lock = threading.RLock()
        self.enabled = True
        self.exists = True
        self.epoch = 3
        self.current_snapshot = (
            '[{"role":"user","content":"hello"}]',
            "snapshot-a",
        )
        self.queued = []
        self.retained = []
        self.hooks = []
        self.purge_calls = []
        self.local_purge_writes = []
        self.provider = object()
        self.connection = sqlite3.connect(":memory:")

        def submit_background(name, fn, *args, **kwargs):
            self.queued.append(
                (name, fn, args, kwargs)
            )
            return object()

        self.guard = HindsightStaleGuard(
            open_db=lambda: sqlite3.connect(":memory:"),
            session_lock=lambda _chat_id, _session_id: self.lock,
            memory_enabled=lambda _db, _chat_id: self.enabled,
            submit_background=submit_background,
            session_exists=lambda _db, _chat_id, _session_id: self.exists,
            read_epoch=lambda _db, _chat_id, _session_id: self.epoch,
            snapshot=lambda _db, _chat_id, _session_id: self.current_snapshot,
            retain_backend=lambda chat_id, session, name, conversation: (
                self.retained.append(
                    (
                        chat_id,
                        session["session_id"],
                        name,
                        conversation,
                    )
                )
            ),
            purge_backend=lambda _db, chat_id, session_id: (
                self.purge_calls.append(
                    (chat_id, session_id)
                )
                or 4
            ),
            write_successful_purge_state=(
                lambda _db, chat_id, session_id:
                self.local_purge_writes.append(
                    (chat_id, session_id)
                )
            ),
            run_post_retain_hooks=(
                lambda _db, chat_id, session, fields, provider_port:
                self.hooks.append(
                    (
                        chat_id,
                        session["session_id"],
                        fields["name"],
                        provider_port,
                    )
                )
            ),
        )
        self.session = {"session_id": "session"}
        self.fields = {"name": "Mira"}

    def tearDown(self):
        self.connection.close()

    def _run_queued_retain(self):
        self.assertEqual(len(self.queued), 1)
        name, fn, args, kwargs = self.queued[0]
        self.assertEqual(name, "hindsight_retain")
        fn(*args, **kwargs)

    def test_current_snapshot_reaches_retain_backend(self):
        self.guard.retain(
            self.connection,
            "chat",
            self.session,
            self.fields,
            provider_port=self.provider,
        )

        self._run_queued_retain()

        self.assertEqual(
            self.retained,
            [
                (
                    "chat",
                    "session",
                    "Mira",
                    self.current_snapshot[0],
                )
            ],
        )
        self.assertEqual(
            self.hooks,
            [("chat", "session", "Mira", self.provider)],
        )

    def test_transcript_mismatch_skips_retain_backend(self):
        self.guard.retain(
            self.connection,
            "chat",
            self.session,
            self.fields,
            provider_port=self.provider,
        )
        self.current_snapshot = (
            '[{"role":"user","content":"edited"}]',
            "snapshot-b",
        )

        self._run_queued_retain()

        self.assertEqual(self.retained, [])

    def test_epoch_mismatch_skips_retain_backend(self):
        self.guard.retain(
            self.connection,
            "chat",
            self.session,
            self.fields,
            provider_port=self.provider,
        )
        self.epoch += 1

        self._run_queued_retain()

        self.assertEqual(self.retained, [])

    def test_deleted_session_skips_retain_backend(self):
        self.guard.retain(
            self.connection,
            "chat",
            self.session,
            self.fields,
            provider_port=self.provider,
        )
        self.exists = False

        self._run_queued_retain()

        self.assertEqual(self.retained, [])

    def test_empty_current_snapshot_skips_retain_backend(self):
        self.guard.retain(
            self.connection,
            "chat",
            self.session,
            self.fields,
            provider_port=self.provider,
        )
        self.current_snapshot = ("", "")

        self._run_queued_retain()

        self.assertEqual(self.retained, [])

    def test_memory_off_still_runs_post_retain_hooks(self):
        self.enabled = False

        self.guard.retain(
            self.connection,
            "chat",
            self.session,
            self.fields,
            provider_port=self.provider,
        )

        self.assertEqual(self.queued, [])
        self.assertEqual(
            self.hooks,
            [("chat", "session", "Mira", self.provider)],
        )

    def test_successful_purge_writes_local_state_after_backend(self):
        order = []

        guard = HindsightStaleGuard(
            open_db=lambda: sqlite3.connect(":memory:"),
            session_lock=lambda _chat_id, _session_id: self.lock,
            memory_enabled=lambda _db, _chat_id: True,
            submit_background=lambda *_args, **_kwargs: None,
            session_exists=lambda *_args: True,
            read_epoch=lambda *_args: 0,
            snapshot=lambda *_args: ("", ""),
            retain_backend=lambda *_args: None,
            purge_backend=lambda *_args: (
                order.append("remote") or 7
            ),
            write_successful_purge_state=lambda *_args: (
                order.append("local")
            ),
            run_post_retain_hooks=lambda *_args: None,
        )

        deleted = guard.purge(
            self.connection,
            "chat",
            "session",
        )

        self.assertEqual(deleted, 7)
        self.assertEqual(order, ["remote", "local"])

    def test_failed_purge_does_not_write_local_state(self):
        original = RuntimeError(
            "Hindsight session memory cleanup failed"
        )

        guard = HindsightStaleGuard(
            open_db=lambda: sqlite3.connect(":memory:"),
            session_lock=lambda _chat_id, _session_id: self.lock,
            memory_enabled=lambda _db, _chat_id: True,
            submit_background=lambda *_args, **_kwargs: None,
            session_exists=lambda *_args: True,
            read_epoch=lambda *_args: 0,
            snapshot=lambda *_args: ("", ""),
            retain_backend=lambda *_args: None,
            purge_backend=lambda *_args: (
                (_ for _ in ()).throw(original)
            ),
            write_successful_purge_state=lambda *_args: (
                self.local_purge_writes.append("unexpected")
            ),
            run_post_retain_hooks=lambda *_args: None,
        )

        with self.assertRaises(RuntimeError) as caught:
            guard.purge(
                self.connection,
                "chat",
                "session",
            )

        self.assertIs(caught.exception, original)
        self.assertEqual(
            self.local_purge_writes,
            [],
        )

    def test_retain_validation_and_purge_are_serialized_per_session(self):
        active = 0
        maximum = 0
        counter_lock = threading.Lock()
        barrier = threading.Barrier(3)
        errors = []

        def enter_backend():
            nonlocal active, maximum
            with counter_lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with counter_lock:
                active -= 1

        guard = HindsightStaleGuard(
            open_db=lambda: sqlite3.connect(":memory:"),
            session_lock=lambda _chat_id, _session_id: self.lock,
            memory_enabled=lambda _db, _chat_id: True,
            submit_background=lambda *_args, **_kwargs: None,
            session_exists=lambda *_args: True,
            read_epoch=lambda *_args: 1,
            snapshot=lambda *_args: ("payload", "hash"),
            retain_backend=lambda *_args: enter_backend(),
            purge_backend=lambda *_args: (
                enter_backend() or 1
            ),
            write_successful_purge_state=lambda *_args: None,
            run_post_retain_hooks=lambda *_args: None,
        )

        def retain_worker():
            try:
                barrier.wait(timeout=2)
                guard._retain_if_current(
                    "chat",
                    {"session_id": "session"},
                    "Mira",
                    "payload",
                    "hash",
                    1,
                )
            except Exception as exc:
                errors.append(exc)

        def purge_worker():
            try:
                barrier.wait(timeout=2)
                guard.purge(
                    self.connection,
                    "chat",
                    "session",
                )
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=retain_worker),
            threading.Thread(target=purge_worker),
        ]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=2)

        self.assertEqual(errors, [])
        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
