"""Tests for sure_client.push_transactions with HTTP mocked via responses.

This is a starter test covering the three things that are easy to get
wrong: sign inversion, NZ-local date conversion, and payload shape.
Extend with edge cases (missing date, fallback name, error responses, etc.)
as the integration matures.
"""

import json
import importlib
import sys

import pytest
import responses


@pytest.fixture(autouse=True)
def _sure_env(monkeypatch):
    monkeypatch.setenv("RUN_SYNC_TO_YNAB", "false")
    monkeypatch.setenv("RUN_SYNC_TO_AB", "false")
    monkeypatch.setenv("RUN_SYNC_TO_SURE", "true")
    monkeypatch.setenv("AKAHU_USER_TOKEN", "akahu-test-user")
    monkeypatch.setenv("AKAHU_APP_TOKEN", "akahu-test-app")
    monkeypatch.setenv("SURE_API_TOKEN", "sure-test-token")
    monkeypatch.setenv("SURE_USE_SIDECAR", "false")
    monkeypatch.delenv("SURE_API_URL", raising=False)
    sys.modules.pop("modules.config", None)


def _reload_config():
    import modules.config

    return importlib.reload(modules.config)


@responses.activate
def test_push_transactions_flips_sign_and_converts_date_to_nz_local(monkeypatch):
    from sure_client import push_transactions, SURE_DEFAULT_URL

    _reload_config()

    responses.add(responses.POST, SURE_DEFAULT_URL, json={"ok": True}, status=200)

    # 2025-06-15 23:30 UTC == 2025-06-16 11:30 NZST. NZ-local date should win.
    # Akahu reports expenses as negative; Sure stores expenses as positive.
    # The client negates so a -42.50 Akahu cafe debit lands as +42.50 in Sure.
    transaction = {
        "_id": "akahu-tx-123",
        "amount": -42.50,
        "date": "2025-06-15T23:30:00.000Z",
        "merchant_name": "Test Cafe",
    }

    push_transactions([transaction], sure_account_id="sure-acc-1")

    assert len(responses.calls) == 1
    sent = json.loads(responses.calls[0].request.body)
    assert sent["transaction"] == {
        "account_id": "sure-acc-1",
        "date": "2025-06-16",
        "amount": 42.50,
        "name": "Test Cafe",
        "notes": "Akahu ID: akahu-tx-123",
        "external_id": "akahu-tx-123",
    }
    assert responses.calls[0].request.headers["X-Api-Key"] == "sure-test-token"


@responses.activate
def test_push_transactions_uses_url_override(monkeypatch):
    from sure_client import push_transactions

    custom_url = "https://sure.example.test/api/v1/transactions"
    monkeypatch.setenv("SURE_API_URL", custom_url)
    _reload_config()
    responses.add(responses.POST, custom_url, json={"ok": True}, status=200)

    push_transactions(
        [{"_id": "x", "amount": 1.00, "date": "2025-01-01T00:00:00Z"}],
        sure_account_id="sure-acc-1",
    )

    assert len(responses.calls) == 1
    assert responses.calls[0].request.url == custom_url


def test_push_transactions_raises_when_token_missing(monkeypatch):
    from sure_client import push_transactions

    monkeypatch.setenv("RUN_SYNC_TO_YNAB", "true")
    monkeypatch.setenv("RUN_SYNC_TO_SURE", "false")
    monkeypatch.setenv("YNAB_BEARER_TOKEN", "ynab-token")
    monkeypatch.delenv("SURE_API_TOKEN", raising=False)
    _reload_config()

    with pytest.raises(RuntimeError, match="SURE_API_TOKEN"):
        push_transactions([{"_id": "x", "amount": 1.0, "date": ""}], "sure-acc-1")
