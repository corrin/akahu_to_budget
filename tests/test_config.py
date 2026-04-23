"""Env-var validation in modules.config."""

import pytest


def test_full_env_both_targets_enabled(full_env, reload_config):
    cfg = reload_config()
    assert cfg.RUN_SYNC_TO_YNAB is True
    assert cfg.RUN_SYNC_TO_AB is True
    assert cfg.AKAHU_HEADERS["Authorization"] == "Bearer akahu-user"
    assert cfg.AKAHU_HEADERS["X-Akahu-ID"] == "akahu-app"
    assert cfg.YNAB_HEADERS == {"Authorization": "Bearer ynab-bearer"}


def test_ynab_disabled_does_not_require_ynab_token(clean_env, reload_config):
    clean_env.setenv("RUN_SYNC_TO_YNAB", "false")
    clean_env.setenv("RUN_SYNC_TO_AB", "true")
    clean_env.setenv("AKAHU_USER_TOKEN", "x")
    clean_env.setenv("AKAHU_APP_TOKEN", "x")
    clean_env.setenv("ACTUAL_SERVER_URL", "https://x")
    clean_env.setenv("ACTUAL_PASSWORD", "p")
    clean_env.setenv("ACTUAL_ENCRYPTION_KEY", "k")
    clean_env.setenv("ACTUAL_SYNC_ID", "s")

    cfg = reload_config()

    assert cfg.RUN_SYNC_TO_YNAB is False
    assert cfg.YNAB_HEADERS is None


def test_ab_disabled_does_not_require_actual_vars(clean_env, reload_config):
    clean_env.setenv("RUN_SYNC_TO_YNAB", "true")
    clean_env.setenv("RUN_SYNC_TO_AB", "false")
    clean_env.setenv("AKAHU_USER_TOKEN", "x")
    clean_env.setenv("AKAHU_APP_TOKEN", "x")
    clean_env.setenv("YNAB_BEARER_TOKEN", "tok")

    cfg = reload_config()

    assert cfg.RUN_SYNC_TO_AB is False
    assert cfg.YNAB_HEADERS == {"Authorization": "Bearer tok"}


def test_missing_flag_fails_loud(clean_env, reload_config):
    # Deliberately no RUN_SYNC_TO_* set.
    with pytest.raises(EnvironmentError, match="RUN_SYNC_TO_YNAB"):
        reload_config()


def test_both_flags_false_fails_loud(clean_env, reload_config):
    clean_env.setenv("RUN_SYNC_TO_YNAB", "false")
    clean_env.setenv("RUN_SYNC_TO_AB", "false")
    with pytest.raises(EnvironmentError, match="must be True"):
        reload_config()


def test_ynab_enabled_but_token_missing_fails_loud(clean_env, reload_config):
    clean_env.setenv("RUN_SYNC_TO_YNAB", "true")
    clean_env.setenv("RUN_SYNC_TO_AB", "false")
    clean_env.setenv("AKAHU_USER_TOKEN", "x")
    clean_env.setenv("AKAHU_APP_TOKEN", "x")
    # YNAB_BEARER_TOKEN deliberately absent
    with pytest.raises(EnvironmentError, match="YNAB_BEARER_TOKEN"):
        reload_config()


def test_ab_enabled_but_actual_server_url_missing_fails_loud(clean_env, reload_config):
    clean_env.setenv("RUN_SYNC_TO_YNAB", "false")
    clean_env.setenv("RUN_SYNC_TO_AB", "true")
    clean_env.setenv("AKAHU_USER_TOKEN", "x")
    clean_env.setenv("AKAHU_APP_TOKEN", "x")
    clean_env.setenv("ACTUAL_PASSWORD", "p")
    clean_env.setenv("ACTUAL_ENCRYPTION_KEY", "k")
    clean_env.setenv("ACTUAL_SYNC_ID", "s")
    # ACTUAL_SERVER_URL deliberately absent
    with pytest.raises(EnvironmentError, match="ACTUAL_SERVER_URL"):
        reload_config()


def test_akahu_tokens_always_required(clean_env, reload_config):
    clean_env.setenv("RUN_SYNC_TO_YNAB", "false")
    clean_env.setenv("RUN_SYNC_TO_AB", "true")
    clean_env.setenv("ACTUAL_SERVER_URL", "https://x")
    clean_env.setenv("ACTUAL_PASSWORD", "p")
    clean_env.setenv("ACTUAL_ENCRYPTION_KEY", "k")
    clean_env.setenv("ACTUAL_SYNC_ID", "s")
    # AKAHU tokens deliberately absent
    with pytest.raises(EnvironmentError, match="AKAHU"):
        reload_config()


def test_optional_flags_default_to_false(full_env, reload_config):
    cfg = reload_config()
    assert cfg.FORCE_REFRESH is False
    assert cfg.DEBUG_SYNC is False


def test_force_refresh_parses_true(full_env, reload_config):
    full_env.setenv("FORCE_REFRESH", "true")
    cfg = reload_config()
    assert cfg.FORCE_REFRESH is True


def test_akahu_endpoint_has_no_trailing_slash(full_env, reload_config):
    """Regression guard for commit 8126129 - trailing slash produced `//`
    in composed URLs and Akahu 404'd."""
    cfg = reload_config()
    assert not cfg.AKAHU_ENDPOINT.endswith("/")
