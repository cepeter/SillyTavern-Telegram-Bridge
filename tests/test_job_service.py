from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import unittest

from bridge.job_service import DurableJob, JobService, JobSubmission


class JobServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.calls = []
        self.recovery_rows = []
        self.submit_result = True
        self.start_result = True

        def enqueue_backend(
            db,
            update_id,
            chat_id,
            session_id,
            telegram_message_id,
            kind,
            payload,
        ):
            self.calls.append(
                (
                    "enqueue",
                    db,
                    update_id,
                    chat_id,
                    session_id,
                    telegram_message_id,
                    kind,
                    payload,
                )
            )
            return 41

        def submit_chat(label, chat_id, worker, *args):
            self.calls.append(
                ("submit_chat", label, chat_id, worker, args)
            )
            return self.submit_result

        self.service = JobService(
            enqueue_backend=enqueue_backend,
            actor_backend=lambda db, job_id:
                self.calls.append(("actor", db, job_id)) or "100",
            schedule_backend=lambda db, job_id:
                self.calls.append(("scheduled", db, job_id)) or True,
            start_backend=lambda db, job_id:
                self.calls.append(("running", db, job_id))
                or self.start_result,
            finish_backend=lambda db, job_id, state, error="":
                self.calls.append(
                    ("finish", db, job_id, state, error)
                )
                or True,
            recover_backend=lambda db, recover_running=True:
                list(self.recovery_rows),
            submit_chat=submit_chat,
        )

    def tearDown(self):
        self.db.close()

    def test_enqueue_delegates_and_returns_job_id(self):
        job_id = self.service.enqueue(
            self.db,
            9,
            "chat",
            "session",
            77,
            "generation",
            {"text": "hello"},
        )
        self.assertEqual(job_id, 41)
        self.assertEqual(self.calls[0][0], "enqueue")
        self.assertEqual(
            self.calls[0][2:],
            (
                9,
                "chat",
                "session",
                77,
                "generation",
                {"text": "hello"},
            ),
        )

    def test_accepted_submit_appends_job_id_and_schedules_once(self):
        worker = lambda *_args: None
        submission = JobSubmission(
            label="generation",
            chat_id="chat",
            worker=worker,
            args=("services", "fields"),
        )

        self.assertTrue(
            self.service.submit(self.db, 41, submission)
        )

        submit = self.calls[0]
        self.assertEqual(
            submit[:3],
            ("submit_chat", "generation", "chat"),
        )
        self.assertIs(submit[3], worker)
        self.assertEqual(
            submit[4],
            ("services", "fields", 41),
        )
        self.assertEqual(
            self.calls[1],
            ("scheduled", self.db, 41),
        )

    def test_submit_can_prepare_worker_without_owning_business_logic(self):
        seen = []
        worker = lambda *_args: None

        def prepare_worker(db, job_id, actual_worker):
            seen.append((db, job_id, actual_worker))

            def guarded(*args):
                return actual_worker(*args)

            return guarded

        service = replace(
            self.service,
            prepare_worker=prepare_worker,
        )
        submission = JobSubmission(
            label="generation",
            chat_id="chat",
            worker=worker,
            args=("services",),
        )

        self.assertTrue(service.submit(self.db, 41, submission))

        self.assertEqual(seen, [(self.db, 41, worker)])
        submit = self.calls[0]
        self.assertIsNot(submit[3], worker)
        self.assertEqual(submit[4], ("services", 41))
        self.assertEqual(
            self.calls[1],
            ("scheduled", self.db, 41),
        )

    def test_rejected_submit_does_not_schedule_or_fail(self):
        self.submit_result = False
        submission = JobSubmission(
            label="generation",
            chat_id="chat",
            worker=lambda: None,
            args=(),
        )

        self.assertFalse(
            self.service.submit(self.db, 41, submission)
        )

        self.assertEqual(
            [call[0] for call in self.calls],
            ["submit_chat"],
        )

    def test_start_complete_fail_and_actor_delegate(self):
        self.assertTrue(self.service.start(self.db, 41))
        self.assertEqual(
            self.service.actor_id(self.db, 41),
            "100",
        )
        self.assertTrue(self.service.complete(self.db, 41))
        self.assertTrue(
            self.service.fail(
                self.db,
                42,
                RuntimeError("boom"),
            )
        )

        self.assertIn(("running", self.db, 41), self.calls)
        self.assertIn(("actor", self.db, 41), self.calls)
        self.assertIn(
            ("finish", self.db, 41, "done", ""),
            self.calls,
        )
        self.assertIn(
            ("finish", self.db, 42, "failed", "boom"),
            self.calls,
        )

    def test_start_false_is_preserved(self):
        self.start_result = False

        self.assertFalse(self.service.start(self.db, 41))
        self.assertEqual(
            self.calls,
            [("running", self.db, 41)],
        )

    def test_recover_decodes_and_submits_durable_job(self):
        self.recovery_rows = [(
            51,
            "chat",
            "stored-session",
            "10",
            "generation",
            json.dumps({
                "text": "hello",
                "model": "stored::model",
                "resolve_active": True,
            }),
        )]
        seen = []

        def resolver(job):
            seen.append(job)
            return JobSubmission(
                label=job.kind,
                chat_id=job.chat_id,
                worker=lambda *_args: None,
                args=("services",),
            )

        self.service.recover(
            self.db,
            resolver,
            recover_running=True,
        )

        self.assertEqual(
            seen,
            [DurableJob(
                job_id=51,
                chat_id="chat",
                session_id="stored-session",
                telegram_message_id=10,
                kind="generation",
                payload={
                    "text": "hello",
                    "model": "stored::model",
                    "resolve_active": True,
                },
            )],
        )
        self.assertIn(
            ("scheduled", self.db, 51),
            self.calls,
        )

    def test_recover_unsupported_kind_fails_exactly_once(self):
        self.recovery_rows = [(
            52,
            "chat",
            "session",
            "11",
            "unknown",
            "{}",
        )]

        self.service.recover(
            self.db,
            lambda _job: None,
            recover_running=False,
        )

        self.assertEqual(
            [
                call
                for call in self.calls
                if call[0] == "finish"
            ],
            [(
                "finish",
                self.db,
                52,
                "failed",
                "unsupported recovered job kind",
            )],
        )

    def test_recover_bad_payload_fails_and_continues(self):
        self.recovery_rows = [
            (53, "chat", "session", "12", "generation", "{bad"),
            (54, "chat", "session", "13", "generation", "{}"),
        ]
        seen = []

        def resolver(job):
            seen.append(job.job_id)
            return JobSubmission(
                label=job.kind,
                chat_id=job.chat_id,
                worker=lambda *_args: None,
                args=(),
            )

        self.service.recover(self.db, resolver)

        failures = [
            call
            for call in self.calls
            if call[0] == "finish"
        ]
        self.assertEqual(failures[0][2:4], (53, "failed"))
        self.assertEqual(seen, [54])
        self.assertIn(("scheduled", self.db, 54), self.calls)

    def test_recover_resolver_exception_fails_and_continues(self):
        self.recovery_rows = [
            (55, "chat", "session", "14", "generation", "{}"),
            (56, "chat", "session", "15", "generation", "{}"),
        ]
        seen = []

        def resolver(job):
            seen.append(job.job_id)
            if job.job_id == 55:
                raise ValueError("bad recovered job")
            return JobSubmission(
                label=job.kind,
                chat_id=job.chat_id,
                worker=lambda *_args: None,
                args=(),
            )

        self.service.recover(self.db, resolver)

        self.assertEqual(seen, [55, 56])
        self.assertIn(
            (
                "finish",
                self.db,
                55,
                "failed",
                "bad recovered job",
            ),
            self.calls,
        )
        self.assertIn(
            ("scheduled", self.db, 56),
            self.calls,
        )

    def test_recover_rejected_submission_remains_unfinished(self):
        self.submit_result = False
        self.recovery_rows = [
            (
                57,
                "chat",
                "session",
                "16",
                "generation",
                "{}",
            ),
        ]

        self.service.recover(
            self.db,
            lambda job: JobSubmission(
                label=job.kind,
                chat_id=job.chat_id,
                worker=lambda *_args: None,
                args=(),
            ),
        )

        self.assertFalse(
            any(
                call[0] in {"scheduled", "finish"}
                for call in self.calls
            )
        )


    def test_recover_uses_prepare_worker_for_recovered_submission(self):
        prepared = []

        def prepare_worker(db, job_id, worker):
            prepared.append((db, job_id, worker))
            return worker

        service = replace(
            self.service,
            prepare_worker=prepare_worker,
        )
        self.recovery_rows = [
            (
                58,
                "chat",
                "session",
                "17",
                "generation",
                "{}",
            ),
        ]
        worker = lambda *_args: None

        service.recover(
            self.db,
            lambda job: JobSubmission(
                label=job.kind,
                chat_id=job.chat_id,
                worker=worker,
                args=(),
            ),
        )

        self.assertEqual(
            prepared,
            [(self.db, 58, worker)],
        )


def function_chunk(source: str, function_name: str) -> str:
    start = source.index(function_name)
    next_function = source.find("\ndef ", start + len(function_name))
    if next_function < 0:
        return source[start:]
    return source[start:next_function]


class JobServiceSourceBoundaryTests(unittest.TestCase):
    def test_backlog_dispatcher_uses_injected_job_service(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "main.py"
        ).read_text(encoding="utf-8")
        chunk = function_chunk(
            source,
            "def make_durable_backlog_dispatcher",
        )
        self.assertNotIn("recover_jobs(", chunk)
        self.assertNotIn("json.loads(", chunk)
        self.assertIn("services.jobs.recover(", chunk)

    def test_job_service_is_ordinary_import_boundary(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "job_service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("bridge.runtime", source)
        self.assertNotIn("bridge.main", source)
        self.assertNotIn("process_message_job", source)
        self.assertNotIn("process_image_job", source)

    def test_main_durable_intake_uses_job_service(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "main.py"
        ).read_text(encoding="utf-8")
        main_chunk = source[source.index("def main()"):]
        self.assertNotIn("enqueue_job(", main_chunk)
        self.assertNotIn(
            "submit_durable_chat_job(",
            main_chunk,
        )
        self.assertIn("services.jobs.enqueue(", main_chunk)
        self.assertIn("services.jobs.submit(", main_chunk)


if __name__ == "__main__":
    unittest.main()
