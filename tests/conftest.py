import pytest

import bridge.common as common


def pytest_sessionfinish(session, exitstatus):
    """Do not let async utility work leak past the pytest process lifetime."""
    common.begin_background_shutdown()
    if not common.drain_background_jobs(timeout=15.0):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture
def app_settings_builder():
    from settings_test_support import SettingsBuilder

    return SettingsBuilder()
