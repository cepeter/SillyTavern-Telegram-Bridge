from application_test_setup import ensure_application_extensions

ensure_application_extensions()

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge.help as _m_help
import bridge.worker_orchestration as _m_workers
import bridge.media as _m_media
import bridge.memory_curator as _m_memory_curator
import bridge.callbacks as _m_callbacks
import bridge.commands as _m_commands
import bridge.database as _m_database
import bridge.message_commands as _m_message_commands
import bridge.telegram as _m_telegram
from bridge.composition import (
    BackgroundRuntime,
    BridgeConfig,
    BridgeServices,
    TelegramRuntime,
)


class FakeJobs:
    def __init__(self):
        self.calls = []
        self.start_result = True
        self.actor = "100"

    def start(self, db, job_id):
        self.calls.append(("start", db, job_id))
        return self.start_result

    def complete(self, db, job_id):
        self.calls.append(("complete", db, job_id))
        return True

    def fail(self, db, job_id, error):
        self.calls.append(("fail", db, job_id, str(error)))
        return True

    def actor_id(self, db, job_id):
        self.calls.append(("actor", db, job_id))
        return self.actor


class JobWorkerServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "jobs.sqlite3"
        self.jobs = FakeJobs()
        config = BridgeConfig(
            bot_token="token",
            api_key="key",
            default_model="provider::model",
            default_character_file="mira.png",
            card_file=Path(self.tmp.name) / "mira.png",
            db_file=self.path,
            allowed_users=frozenset(),
        )
        config.card_file.write_bytes(b"card")
        self.sent = []
        self.services = BridgeServices(
            config=config,
            db_factory=lambda: _m_memory_curator.db_connect(self.path),
            telegram=TelegramRuntime(
                request=lambda *_args, **_kwargs: {},
                send_text=lambda *args, **_kwargs:
                    self.sent.append(args),
            ),
            background=BackgroundRuntime(
                submit_chat=lambda *_args, **_kwargs: True,
                register_backlog_dispatcher=lambda _callback: None,
                begin_shutdown=lambda: None,
            ),
            jobs=self.jobs,
            group_director=object(),
            memory=object(),
            persona=object(),
            sync=object(),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def lifecycle_names(self):
        return [
            call[0]
            for call in self.jobs.calls
            if call[0] != "actor"
        ]

    def test_message_worker_stops_when_job_start_is_rejected(self):
        self.jobs.start_result = False
        with patch.object(
            _m_message_commands,
            "process_message",
            side_effect=AssertionError(
                "business workflow must not run"
            ),
        ):
            _m_workers.process_message_job(
                self.services,
                {"name": "Mira"},
                "chat",
                "hello",
                10,
                job_id=41,
            )

        self.assertEqual(self.jobs.calls[0][0], "start")
        self.assertFalse(
            any(
                call[0] in {"complete", "fail"}
                for call in self.jobs.calls
            )
        )

    def test_message_worker_success_uses_job_service(self):
        with patch.object(_m_workers, "committed_assistant_for_message",
            return_value=None,
        ), patch.object(_m_workers, "process_message",
        ):
            _m_workers.process_message_job(
                self.services,
                {"name": "Mira"},
                "chat",
                "hello",
                10,
                job_id=41,
            )

        self.assertEqual(
            self.lifecycle_names(),
            ["start", "complete"],
        )

    def test_message_worker_failure_uses_job_service(self):
        with patch.object(
            _m_database,
            "committed_assistant_for_message",
            return_value=None,
        ), patch.object(
            _m_message_commands,
            "process_message",
            side_effect=RuntimeError("message boom"),
        ):
            _m_workers.process_message_job(
                self.services,
                {"name": "Mira"},
                "chat",
                "hello",
                10,
                job_id=41,
            )

        self.assertEqual(
            self.lifecycle_names(),
            ["start", "fail"],
        )
        self.assertEqual(
            self.sent[-1][2],
            "The character backend failed for this message. Use /retry or /status.",
        )

    def test_image_worker_success_and_failure_use_job_service(self):
        with patch.object(_m_workers, "committed_assistant_for_message",
            return_value=None,
        ), patch.object(_m_workers, "process_telegram_image",
        ):
            _m_workers.process_image_job(
                self.services,
                "chat",
                "file",
                "caption",
                5,
                11,
                job_id=42,
            )
        self.assertEqual(
            self.lifecycle_names(),
            ["start", "complete"],
        )

        self.jobs.calls.clear()
        with patch.object(_m_workers, "committed_assistant_for_message",
            return_value=None,
        ), patch.object(_m_workers, "process_telegram_image",
            side_effect=RuntimeError("image boom"),
        ):
            _m_workers.process_image_job(
                self.services,
                "chat",
                "file",
                "caption",
                5,
                11,
                job_id=42,
            )
        self.assertEqual(
            self.lifecycle_names(),
            ["start", "fail"],
        )
        self.assertEqual(
            self.sent[-1][2],
            "Image processing failed. The selected model may not support vision.",
        )

    def test_callback_worker_success_and_failure_use_job_service(self):
        callback = {
            "id": "cb",
            "from": {"id": "100"},
            "message": {"chat": {"id": "chat"}},
        }
        with patch.object(_m_workers, "operation_was_applied",
            return_value=False,
        ), patch.object(_m_workers, "process_callback",
        ):
            _m_workers.process_callback_job(
                self.services,
                "chat",
                callback,
                job_id=43,
            )
        self.assertEqual(
            self.lifecycle_names(),
            ["start", "complete"],
        )

        self.jobs.calls.clear()
        with patch.object(_m_workers, "operation_was_applied",
            return_value=False,
        ), patch.object(_m_workers, "process_callback",
            side_effect=RuntimeError("callback boom"),
        ):
            _m_workers.process_callback_job(
                self.services,
                "chat",
                callback,
                job_id=43,
            )
        self.assertEqual(
            self.lifecycle_names(),
            ["start", "fail"],
        )
        self.assertEqual(
            self.sent[-1][2],
            "Callback processing failed; try the command again.",
        )

    def test_callback_already_applied_completes_without_business_call(self):
        callback = {
            "id": "cb",
            "from": {"id": "100"},
            "message": {"chat": {"id": "chat"}},
        }
        with patch.object(
            _m_database,
            "operation_was_applied",
            return_value=True,
        ), patch.object(
            _m_callbacks,
            "process_callback",
            side_effect=AssertionError(
                "callback must not be applied twice"
            ),
        ):
            _m_workers.process_callback_job(
                self.services,
                "chat",
                callback,
                job_id=43,
            )

        self.assertEqual(
            self.lifecycle_names(),
            ["start", "complete"],
        )

    def test_edit_worker_success_and_failure_use_job_service(self):
        with patch.object(_m_workers, "edit_telegram_user_message",
        ):
            _m_workers.process_edit_job(
                self.services,
                "chat",
                77,
                "edited",
                job_id=44,
            )
        self.assertEqual(
            self.lifecycle_names(),
            ["start", "complete"],
        )

        self.jobs.calls.clear()
        with patch.object(_m_workers, "edit_telegram_user_message",
            side_effect=RuntimeError("edit boom"),
        ), patch.object(
            _m_workers,
            "native_edit_committed_after_failure",
            return_value=False,
        ):
            _m_workers.process_edit_job(
                self.services,
                "chat",
                77,
                "edited",
                job_id=44,
            )
        self.assertEqual(
            self.lifecycle_names(),
            ["start", "fail"],
        )
        self.assertEqual(
            self.sent[-1][2],
            "Native message edit failed; the previous branch was preserved.",
        )

    def test_locally_committed_edit_failure_completes_instead_of_failing(self):
        with patch.object(
            _m_commands,
            "edit_telegram_user_message",
            side_effect=RuntimeError("provider delivery failed"),
        ), patch.object(
            _m_workers,
            "native_edit_committed_after_failure",
            return_value=True,
        ):
            _m_workers.process_edit_job(
                self.services,
                "chat",
                77,
                "edited",
                job_id=45,
            )

        self.assertEqual(
            self.lifecycle_names(),
            ["start", "complete"],
        )
        self.assertFalse(
            any(call[0] == "fail" for call in self.jobs.calls)
        )

    def test_voice_worker_success_and_failure_use_job_service(self):
        with patch.object(
            _m_database,
            "committed_assistant_for_message",
            return_value=None,
        ), patch.object(
            _m_media,
            "process_voice_message",
        ) as voice_message:
            _m_media.process_voice_job(
                self.services,
                {"name": "Mira"},
                "chat",
                {"file_id": "voice"},
                78,
                job_id=46,
            )
        self.assertEqual(
            self.lifecycle_names(),
            ["start", "complete"],
        )
        self.assertIs(
            voice_message.call_args.kwargs["services"],
            self.services,
        )

        self.jobs.calls.clear()
        with patch.object(
            _m_database,
            "committed_assistant_for_message",
            return_value=None,
        ), patch.object(
            _m_media,
            "process_voice_message",
            side_effect=RuntimeError("voice boom"),
        ):
            _m_media.process_voice_job(
                self.services,
                {"name": "Mira"},
                "chat",
                {"file_id": "voice"},
                78,
                job_id=46,
            )
        self.assertEqual(
            self.lifecycle_names(),
            ["start", "fail"],
        )
        self.assertEqual(
            self.sent[-1][2],
            "Voice processing failed. Use /voice_input status to check transcription settings.",
        )

    def test_document_worker_success_and_failure_use_job_service(self):
        with patch.object(_m_help, "import_telegram_document",
        ):
            _m_help.process_document_job(
                self.services,
                "chat",
                {"file_name": "notes.txt"},
                79,
                job_id=47,
            )
        self.assertEqual(
            self.lifecycle_names(),
            ["start", "complete"],
        )

        self.jobs.calls.clear()
        with patch.object(_m_help, "import_telegram_document",
            side_effect=RuntimeError("document boom"),
        ):
            _m_help.process_document_job(
                self.services,
                "chat",
                {"file_name": "notes.txt"},
                79,
                job_id=47,
            )
        self.assertEqual(
            self.lifecycle_names(),
            ["start", "fail"],
        )
        self.assertEqual(
            self.sent[-1][2],
            "Document import failed. Check the file format and size limits.",
        )

    def test_message_callback_and_voice_actor_lookup_use_job_service(self):
        self.jobs.actor = "777"

        with patch.object(
            _m_workers,
            "committed_assistant_for_message",
            return_value=None,
        ), patch.object(
            _m_workers,
            "process_message",
        ) as process_message:
            _m_workers.process_message_job(
                self.services,
                {"name": "Mira"},
                "chat",
                "hello",
                80,
                job_id=48,
            )
        self.assertEqual(
            process_message.call_args.kwargs["actor_id"],
            "777",
        )

        self.jobs.calls.clear()
        callback = {
            "id": "cb",
            "from": {"id": "100"},
            "message": {"chat": {"id": "chat"}},
        }
        with patch.object(
            _m_workers,
            "operation_was_applied",
            return_value=False,
        ), patch.object(
            _m_workers,
            "process_callback",
        ) as process_callback:
            _m_workers.process_callback_job(
                self.services,
                "chat",
                callback,
                job_id=49,
            )
        self.assertEqual(
            process_callback.call_args.kwargs["actor_id"],
            "777",
        )

        self.jobs.calls.clear()
        with patch.object(
            _m_media,
            "committed_assistant_for_message",
            return_value=None,
        ), patch.object(
            _m_media,
            "process_voice_message",
        ) as process_voice_message:
            _m_media.process_voice_job(
                self.services,
                {"name": "Mira"},
                "chat",
                {"file_id": "voice"},
                81,
                job_id=50,
            )
        self.assertEqual(
            process_voice_message.call_args.kwargs["actor_id"],
            "777",
        )


if __name__ == "__main__":
    unittest.main()
