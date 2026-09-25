import pytest

import bridge.background as _background


def pytest_sessionfinish(session, exitstatus):
    """Do not let async utility work leak past the pytest process lifetime."""
    _background.begin_background_shutdown()
    if not _background.drain_background_jobs(timeout=15.0):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture
def app_settings_builder():
    from settings_test_support import SettingsBuilder

    return SettingsBuilder()


@pytest.fixture(autouse=True)
def reject_external_test_connections(monkeypatch):
    """External calls must be mocked, even if application code catches failures."""
    import socket

    from network_test_support import guard_connect

    attempts = []
    monkeypatch.setattr(socket.socket, "connect", guard_connect(socket.socket.connect, attempts))
    monkeypatch.setattr(socket.socket, "connect_ex", guard_connect(socket.socket.connect_ex, attempts))
    yield
    assert not attempts, "Unexpected external socket attempt during test; provide an explicit transport mock"
