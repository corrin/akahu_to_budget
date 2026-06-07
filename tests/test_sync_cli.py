"""Tests for sync_cli argument helpers."""

import sys
from unittest.mock import Mock


def test_set_env_override_ignores_missing_value(monkeypatch):
    from sync_cli import set_env_override

    monkeypatch.delenv("MAPPING_FILE", raising=False)

    set_env_override("MAPPING_FILE", None)

    assert "MAPPING_FILE" not in __import__("os").environ


def test_set_env_override_preserves_empty_string(monkeypatch):
    from sync_cli import set_env_override

    set_env_override("LOG_FILE", "")

    assert __import__("os").environ["LOG_FILE"] == ""


def test_refresh_only_calls_refresh_and_skips_sync(clean_env, reload_config, monkeypatch):
    clean_env.setenv("RUN_SYNC_TO_YNAB", "true")
    clean_env.setenv("RUN_SYNC_TO_AB", "false")
    clean_env.setenv("AKAHU_USER_TOKEN", "akahu-user")
    clean_env.setenv("AKAHU_APP_TOKEN", "akahu-app")
    clean_env.setenv("YNAB_BEARER_TOKEN", "ynab-bearer")
    reload_config()
    import modules.sync_runner as sync_runner
    from sync_cli import main

    refresh = Mock()
    run_sync = Mock()
    monkeypatch.setattr(sync_runner, "refresh_akahu", refresh)
    monkeypatch.setattr(sync_runner, "run_sync", run_sync)
    monkeypatch.setattr(sys, "argv", ["sync_cli.py", "--refresh-only"])

    main()

    refresh.assert_called_once_with()
    run_sync.assert_not_called()


def test_skip_akahu_refresh_passes_through(clean_env, reload_config, monkeypatch):
    clean_env.setenv("RUN_SYNC_TO_YNAB", "true")
    clean_env.setenv("RUN_SYNC_TO_AB", "false")
    clean_env.setenv("AKAHU_USER_TOKEN", "akahu-user")
    clean_env.setenv("AKAHU_APP_TOKEN", "akahu-app")
    clean_env.setenv("YNAB_BEARER_TOKEN", "ynab-bearer")
    reload_config()
    import modules.sync_runner as sync_runner
    from sync_cli import main

    refresh = Mock()
    run_sync = Mock()
    monkeypatch.setattr(sync_runner, "refresh_akahu", refresh)
    monkeypatch.setattr(sync_runner, "run_sync", run_sync)
    monkeypatch.setattr(sys, "argv", ["sync_cli.py", "--skip-akahu-refresh"])

    main()

    refresh.assert_not_called()
    run_sync.assert_called_once_with(
        None,
        debug_mode=None,
        skip_akahu_refresh=True,
    )


def test_default_cli_still_refreshes_before_sync(clean_env, reload_config, monkeypatch):
    clean_env.setenv("RUN_SYNC_TO_YNAB", "true")
    clean_env.setenv("RUN_SYNC_TO_AB", "false")
    clean_env.setenv("AKAHU_USER_TOKEN", "akahu-user")
    clean_env.setenv("AKAHU_APP_TOKEN", "akahu-app")
    clean_env.setenv("YNAB_BEARER_TOKEN", "ynab-bearer")
    reload_config()
    import modules.sync_runner as sync_runner
    from sync_cli import main

    refresh = Mock()
    run_sync = Mock()
    monkeypatch.setattr(sync_runner, "refresh_akahu", refresh)
    monkeypatch.setattr(sync_runner, "run_sync", run_sync)
    monkeypatch.setattr(sys, "argv", ["sync_cli.py"])

    main()

    refresh.assert_not_called()
    run_sync.assert_called_once_with(
        None,
        debug_mode=None,
        skip_akahu_refresh=False,
    )
