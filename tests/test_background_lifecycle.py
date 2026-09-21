import concurrent.futures
import unittest

from runtime_test_facade import runtime as rt


class BackgroundLifecycleTests(unittest.TestCase):
    def test_drain_background_jobs_observes_tracked_futures(self):
        future = concurrent.futures.Future()
        with rt._BACKGROUND_STATE_LOCK:
            rt._BACKGROUND_FUTURES.add(future)
        try:
            self.assertFalse(rt.drain_background_jobs(timeout=0.0))
            future.set_result(None)
            self.assertTrue(rt.drain_background_jobs(timeout=0.1))
        finally:
            with rt._BACKGROUND_STATE_LOCK:
                rt._BACKGROUND_FUTURES.discard(future)

    def test_begin_shutdown_blocks_new_dispatch_without_destroying_executors(self):
        old_accepting = rt._BACKGROUND_ACCEPTING
        old_dispatcher = rt._DURABLE_BACKLOG_DISPATCHER
        try:
            rt._BACKGROUND_ACCEPTING = True
            rt._DURABLE_BACKLOG_DISPATCHER = lambda: None
            rt.begin_background_shutdown()
            self.assertFalse(rt.background_jobs_accepting())
            self.assertIsNone(rt._DURABLE_BACKLOG_DISPATCHER)
            self.assertFalse(rt.submit_background("tts", lambda: None))
        finally:
            rt._BACKGROUND_ACCEPTING = old_accepting
            rt._DURABLE_BACKLOG_DISPATCHER = old_dispatcher


if __name__ == "__main__":
    unittest.main()
