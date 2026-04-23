"""Shared fixtures for the akahu_to_budget test suite.

The app's own `modules.config` performs env-var validation at import time.
To test alternate env combinations we set env vars via `monkeypatch` and
then `importlib.reload(modules.config)` inside each test. These fixtures
make that plumbing reusable.
"""

import importlib
import os
import sys

import pytest


@pytest.fixture
def clean_env(monkeypatch):
    """Wipe every env var modules.config looks at so each test starts blank."""
    keys = [
        "RUN_SYNC_TO_YNAB",
        "RUN_SYNC_TO_AB",
        "FORCE_REFRESH",
        "DEBUG_SYNC",
        "AKAHU_USER_TOKEN",
        "AKAHU_APP_TOKEN",
        "YNAB_BEARER_TOKEN",
        "ACTUAL_SERVER_URL",
        "ACTUAL_PASSWORD",
        "ACTUAL_ENCRYPTION_KEY",
        "ACTUAL_SYNC_ID",
    ]
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


@pytest.fixture
def reload_config(monkeypatch):
    """Return a function that reloads modules.config and returns the module.

    Use after setting env vars so the reloaded module picks them up.

    config.py calls `load_dotenv(override=True)` at import-time, which
    would otherwise read the developer's real .env file and clobber any
    test-set env vars. We neuter it during the reload so tests are
    actually hermetic.
    """

    def _reload():
        import dotenv

        monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: False)

        if "modules.config" not in sys.modules:
            import modules.config  # noqa: F401
        cfg = importlib.reload(sys.modules["modules.config"])

        # Downstream modules cache `from modules.config import NAME`
        # references at their own import time. Reload them too so they
        # pick up the freshly-reloaded config values.
        for mod_name in (
            "modules.account_fetcher",
            "modules.transaction_handler",
            "modules.sync_handler",
            "modules.webhook_handler",
        ):
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])

        return cfg

    return _reload


@pytest.fixture
def full_env(clean_env):
    """Populate every env var config needs for a 'both targets enabled' run."""
    clean_env.setenv("RUN_SYNC_TO_YNAB", "true")
    clean_env.setenv("RUN_SYNC_TO_AB", "true")
    clean_env.setenv("AKAHU_USER_TOKEN", "akahu-user")
    clean_env.setenv("AKAHU_APP_TOKEN", "akahu-app")
    clean_env.setenv("YNAB_BEARER_TOKEN", "ynab-bearer")
    clean_env.setenv("ACTUAL_SERVER_URL", "https://actual.example.test")
    clean_env.setenv("ACTUAL_PASSWORD", "pw")
    clean_env.setenv("ACTUAL_ENCRYPTION_KEY", "k")
    clean_env.setenv("ACTUAL_SYNC_ID", "sync-id")
    return clean_env
