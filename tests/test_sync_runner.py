"""Tests for shared sync runner behavior."""

import importlib
import logging
from types import SimpleNamespace

import pytest


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


def _run_sync_with_fakes(
    monkeypatch, sync_runner, mapping, link_errors=(), calls=None
):
    from contextlib import contextmanager

    calls = [] if calls is None else calls
    fake_client = SimpleNamespace(download_budget=lambda: calls.append("download"))

    @contextmanager
    def fake_actual_client():
        yield fake_client

    def fake_refresh(client, mapping_file):
        calls.append("refresh_links")
        return {"acc_from_actual": {"actual_account_id": "act-1"}}, list(link_errors)

    def fake_sync_to_ab(client, mapping_list, debug_mode=None):
        calls.append(("sync_to_ab", sorted(mapping_list)))
        return 0

    monkeypatch.setattr(sync_runner, "get_actual_client", fake_actual_client)
    monkeypatch.setattr(sync_runner, "refresh_actual_links", fake_refresh)
    monkeypatch.setattr(sync_runner, "sync_to_ab", fake_sync_to_ab)
    monkeypatch.setattr(
        sync_runner, "load_existing_mapping", lambda *a, **kw: ({}, {}, {}, mapping)
    )

    sync_runner.run_sync(skip_akahu_refresh=True)
    return calls


def test_actual_owned_links_regenerate_before_import(
    full_env, reload_config, monkeypatch
):
    full_env.setenv("RUN_SYNC_TO_YNAB", "false")
    full_env.setenv("ACTUAL_ACCOUNT_LINKS", "actual")
    reload_config()
    import modules.sync_runner as sync_runner

    sync_runner = importlib.reload(sync_runner)
    calls = _run_sync_with_fakes(monkeypatch, sync_runner, {"acc_from_file": {}})

    assert calls == ["download", "refresh_links", ("sync_to_ab", ["acc_from_actual"])]


def test_mapping_file_links_do_not_touch_actual_links(
    full_env, reload_config, monkeypatch
):
    full_env.setenv("RUN_SYNC_TO_YNAB", "false")
    reload_config()
    import modules.sync_runner as sync_runner

    sync_runner = importlib.reload(sync_runner)
    calls = _run_sync_with_fakes(monkeypatch, sync_runner, {"acc_from_file": {}})

    assert calls == ["download", ("sync_to_ab", ["acc_from_file"])]


def test_broken_actual_link_fails_run_after_syncing_the_rest(
    full_env, reload_config, monkeypatch
):
    full_env.setenv("RUN_SYNC_TO_YNAB", "false")
    full_env.setenv("ACTUAL_ACCOUNT_LINKS", "actual")
    reload_config()
    import modules.sync_runner as sync_runner

    sync_runner = importlib.reload(sync_runner)
    calls = []
    with pytest.raises(RuntimeError, match="Westpac CC is broken"):
        _run_sync_with_fakes(
            monkeypatch, sync_runner, {},
            link_errors=["Westpac CC is broken"], calls=calls,
        )

    assert ("sync_to_ab", ["acc_from_actual"]) in calls
