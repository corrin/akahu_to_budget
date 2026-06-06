"""Env-var validation in modules.config."""

import json

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
    clean_env.setenv("RUN_SYNC_TO_SURE", "false")
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
    """Call sites compose as `f'{AKAHU_ENDPOINT}/accounts'` - a trailing
    slash here produces `//` and Akahu returns 404."""
    cfg = reload_config()
    assert not cfg.AKAHU_ENDPOINT.endswith("/")


def test_mapping_log_paths_and_daily_sync_default_for_existing_deployments(
    full_env, reload_config
):
    cfg = reload_config()

    assert cfg.MAPPING_FILE == "akahu_budget_mapping.json"
    assert cfg.LOG_FILE == "app.log"
    assert cfg.SYNC_INTERVAL == 86400


def test_env_can_override_mapping_log_and_interval(full_env, reload_config):
    full_env.setenv("MAPPING_FILE", "/tmp/custom-mapping.json")
    full_env.setenv("LOG_FILE", "")
    full_env.setenv("SYNC_INTERVAL", "300")

    cfg = reload_config()

    assert cfg.MAPPING_FILE == "/tmp/custom-mapping.json"
    assert cfg.LOG_FILE is None
    assert cfg.SYNC_INTERVAL == 300


def test_home_assistant_options_override_env(clean_env, reload_config, tmp_path):
    options_file = tmp_path / "options.json"
    options_file.write_text(
        json.dumps(
            {
                "RUN_SYNC_TO_YNAB": False,
                "RUN_SYNC_TO_AB": True,
                "RUN_SYNC_TO_SURE": False,
                "AKAHU_USER_TOKEN": "ha-akahu-user",
                "AKAHU_APP_TOKEN": "ha-akahu-app",
                "ACTUAL_SERVER_URL": "https://actual.ha.test",
                "ACTUAL_PASSWORD": "ha-pw",
                "ACTUAL_ENCRYPTION_KEY": "ha-key",
                "ACTUAL_SYNC_ID": "ha-sync",
                "mapping_file": "/data/akahu_budget_mapping.json",
                "log_file": "",
                "sync_interval": 600,
            }
        ),
        encoding="utf-8",
    )

    clean_env.setenv("RUN_SYNC_TO_YNAB", "true")
    clean_env.setenv("RUN_SYNC_TO_AB", "false")
    clean_env.setenv("AKAHU_TO_BUDGET_OPTIONS_FILE", str(options_file))
    cfg = reload_config()

    assert cfg.RUN_SYNC_TO_YNAB is False
    assert cfg.RUN_SYNC_TO_AB is True
    assert cfg.RUN_SYNC_TO_SURE is False
    assert cfg.ENVs["AKAHU_USER_TOKEN"] == "ha-akahu-user"
    assert cfg.MAPPING_FILE == "/data/akahu_budget_mapping.json"
    assert cfg.LOG_FILE is None
    assert cfg.SYNC_INTERVAL == 600


def test_invalid_home_assistant_options_json_fails_loud(clean_env, reload_config, tmp_path):
    options_file = tmp_path / "options.json"
    options_file.write_text("{not-json", encoding="utf-8")
    clean_env.setenv("AKAHU_TO_BUDGET_OPTIONS_FILE", str(options_file))

    with pytest.raises(EnvironmentError, match="Invalid JSON"):
        reload_config()


def test_sure_enabled_requires_sure_api_token(clean_env, reload_config):
    clean_env.setenv("RUN_SYNC_TO_YNAB", "false")
    clean_env.setenv("RUN_SYNC_TO_AB", "false")
    clean_env.setenv("RUN_SYNC_TO_SURE", "true")
    clean_env.setenv("AKAHU_USER_TOKEN", "x")
    clean_env.setenv("AKAHU_APP_TOKEN", "x")

    with pytest.raises(EnvironmentError, match="SURE_API_TOKEN"):
        reload_config()


def test_home_assistant_options_support_sure_settings(clean_env, reload_config, tmp_path):
    options_file = tmp_path / "options.json"
    options_file.write_text(
        json.dumps(
            {
                "RUN_SYNC_TO_YNAB": False,
                "RUN_SYNC_TO_AB": False,
                "RUN_SYNC_TO_SURE": True,
                "AKAHU_USER_TOKEN": "ha-akahu-user",
                "AKAHU_APP_TOKEN": "ha-akahu-app",
                "SURE_API_TOKEN": "ha-sure-token",
                "SURE_API_URL": "https://sure.example.test/api/v1/transactions",
                "SURE_USE_SIDECAR": False,
            }
        ),
        encoding="utf-8",
    )

    clean_env.setenv("AKAHU_TO_BUDGET_OPTIONS_FILE", str(options_file))
    cfg = reload_config()

    assert cfg.RUN_SYNC_TO_SURE is True
    assert cfg.ENVs["SURE_API_TOKEN"] == "ha-sure-token"
    assert cfg.ENVs["SURE_API_URL"] == "https://sure.example.test/api/v1/transactions"
    assert cfg.ENVs["SURE_USE_SIDECAR"] == "False"


def test_explicit_missing_home_assistant_options_file_fails_loud(
    clean_env, reload_config, tmp_path
):
    clean_env.setenv("AKAHU_TO_BUDGET_OPTIONS_FILE", str(tmp_path / "missing.json"))

    with pytest.raises(EnvironmentError, match="Options file not found"):
        reload_config()
