import concurrent.futures
import unittest

from dependency_patch import dependency_module

_m_common = dependency_module("bridge.common")
_m_main = dependency_module("bridge.main")
_m_memory_curator = dependency_module("bridge.memory_curator")


class BackgroundLifecycleTests(unittest.TestCase):
    def test_drain_background_jobs_observes_tracked_futures(self):
        future = concurrent.futures.Future()
        with _m_common._BACKGROUND_STATE_LOCK:
            _m_common._BACKGROUND_FUTURES.add(future)
        try:
            self.assertFalse(_m_common.drain_background_jobs(timeout=0.0))
            future.set_result(None)
            self.assertTrue(_m_common.drain_background_jobs(timeout=0.1))
        finally:
            with _m_common._BACKGROUND_STATE_LOCK:
                _m_common._BACKGROUND_FUTURES.discard(future)

    def test_begin_shutdown_blocks_new_dispatch_without_destroying_executors(self):
        old_accepting = _m_common._BACKGROUND_ACCEPTING
        old_dispatcher = _m_common._DURABLE_BACKLOG_DISPATCHER
        try:
            _m_common._BACKGROUND_ACCEPTING = True
            _m_common._DURABLE_BACKLOG_DISPATCHER = lambda: None
            _m_main.begin_background_shutdown()
            self.assertFalse(_m_common.background_jobs_accepting())
            self.assertIsNone(_m_common._DURABLE_BACKLOG_DISPATCHER)
            self.assertFalse(_m_memory_curator.submit_background("tts", lambda: None))
        finally:
            _m_common._BACKGROUND_ACCEPTING = old_accepting
            _m_common._DURABLE_BACKLOG_DISPATCHER = old_dispatcher


if __name__ == "__main__":
    unittest.main()
