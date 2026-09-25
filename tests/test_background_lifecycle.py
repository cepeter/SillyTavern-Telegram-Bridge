from application_test_setup import ensure_application_extensions

ensure_application_extensions()

import concurrent.futures
import unittest
from unittest.mock import Mock, patch

import bridge.common as _m_common
import bridge.main as _m_main
import bridge.memory_curator as _m_memory_curator


class BackgroundLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Simulate unrelated work already tracked on the same pytest-xdist worker.
        cls._preexisting_future = concurrent.futures.Future()
        with _m_common._BACKGROUND_STATE_LOCK:
            _m_common._BACKGROUND_FUTURES.add(cls._preexisting_future)

    @classmethod
    def tearDownClass(cls):
        if not cls._preexisting_future.done():
            cls._preexisting_future.set_result(None)
        with _m_common._BACKGROUND_STATE_LOCK:
            _m_common._BACKGROUND_FUTURES.discard(cls._preexisting_future)

    def setUp(self):
        with _m_common._BACKGROUND_STATE_LOCK:
            self._background_futures_before = _m_common._BACKGROUND_FUTURES
            _m_common._BACKGROUND_FUTURES = set()
        self.addCleanup(self._restore_background_futures)

    def _restore_background_futures(self):
        with _m_common._BACKGROUND_STATE_LOCK:
            _m_common._BACKGROUND_FUTURES = self._background_futures_before

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

    def test_executor_for_creates_only_requested_pool_and_reuses_it(self):
        old_generation = _m_common._GENERATION_EXECUTOR
        old_utility = _m_common._UTILITY_EXECUTOR
        _m_common._GENERATION_EXECUTOR = None
        _m_common._UTILITY_EXECUTOR = None
        try:
            generation = Mock()
            utility = Mock()
            with patch.object(
                _m_common.concurrent.futures,
                "ThreadPoolExecutor",
                side_effect=[generation, utility],
            ) as constructor:
                first = _m_common._executor_for("generation")
                second = _m_common._executor_for("retry")
                third = _m_common._executor_for("tts")

            self.assertIs(first, generation)
            self.assertIs(second, generation)
            self.assertIs(third, utility)
            self.assertEqual(constructor.call_count, 2)
        finally:
            _m_common._GENERATION_EXECUTOR = old_generation
            _m_common._UTILITY_EXECUTOR = old_utility

    def test_shutdown_clears_instantiated_executors_and_is_repeatable(self):
        old_generation = _m_common._GENERATION_EXECUTOR
        old_utility = _m_common._UTILITY_EXECUTOR
        old_accepting = _m_common._BACKGROUND_ACCEPTING
        generation = Mock()
        utility = Mock()
        try:
            _m_common._GENERATION_EXECUTOR = generation
            _m_common._UTILITY_EXECUTOR = utility
            _m_common._BACKGROUND_ACCEPTING = True

            self.assertTrue(_m_common.shutdown_background_executors(timeout=0.0))
            self.assertIsNone(_m_common._GENERATION_EXECUTOR)
            self.assertIsNone(_m_common._UTILITY_EXECUTOR)
            generation.shutdown.assert_called_once_with(
                wait=True,
                cancel_futures=False,
            )
            utility.shutdown.assert_called_once_with(
                wait=True,
                cancel_futures=False,
            )

            self.assertTrue(_m_common.shutdown_background_executors(timeout=0.0))
        finally:
            _m_common._GENERATION_EXECUTOR = old_generation
            _m_common._UTILITY_EXECUTOR = old_utility
            _m_common._BACKGROUND_ACCEPTING = old_accepting


if __name__ == "__main__":
    unittest.main()
