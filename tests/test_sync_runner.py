"""Tests for shared sync runner behavior."""

import importlib
import logging
from types import SimpleNamespace


def test_actual_client_logs_server_info(full_env, reload_config, monkeypatch, caplog):
    reload_config()

    import actual
    import modules.sync_runner as sync_runner

    sync_runner = importlib.reload(sync_runner)

    fake_client = SimpleNamespace(
        info=lambda: SimpleNamespace(
            build=SimpleNamespace(name="Actual Budget", version="26.5.0")
        )
    )

    class FakeActual:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return fake_client

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(actual, "Actual", FakeActual)

    with caplog.at_level(logging.INFO):
        with sync_runner.get_actual_client() as client:
            assert client is fake_client

    assert "Actual server info: Actual Budget version 26.5.0" in caplog.text
