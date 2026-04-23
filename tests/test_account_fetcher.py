"""Tests for modules.account_fetcher with HTTP mocked via responses."""

import logging

import pytest
import requests
import responses


@pytest.fixture(autouse=True)
def _env_for_account_fetcher(full_env, reload_config):
    """account_fetcher imports from modules.config at import time, so we
    need config loaded before account_fetcher is imported/used."""
    reload_config()


@responses.activate
def test_trigger_akahu_refresh_success(caplog):
    from modules.account_fetcher import trigger_akahu_refresh

    responses.add(
        responses.POST,
        "https://api.akahu.io/v1/refresh",
        json={"success": True},
        status=200,
    )

    with caplog.at_level(logging.INFO):
        trigger_akahu_refresh()

    assert any(
        "Triggered Akahu refresh" in rec.getMessage() for rec in caplog.records
    )
    assert len(responses.calls) == 1


@responses.activate
def test_trigger_akahu_refresh_http_500_warns_and_returns(caplog):
    from modules.account_fetcher import trigger_akahu_refresh

    responses.add(
        responses.POST,
        "https://api.akahu.io/v1/refresh",
        json={"error": "internal"},
        status=500,
    )

    with caplog.at_level(logging.WARNING):
        trigger_akahu_refresh()

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("refresh request failed" in w.getMessage() for w in warnings)


@responses.activate
def test_trigger_akahu_refresh_connection_error_warns_and_returns(caplog):
    from modules.account_fetcher import trigger_akahu_refresh

    responses.add(
        responses.POST,
        "https://api.akahu.io/v1/refresh",
        body=requests.ConnectionError("simulated outage"),
    )

    with caplog.at_level(logging.WARNING):
        trigger_akahu_refresh()

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "simulated outage" in w.getMessage() for w in warnings
    ), "Connection-error context should be logged for diagnostics"


@responses.activate
def test_trigger_akahu_refresh_passes_auth_headers():
    from modules.account_fetcher import trigger_akahu_refresh

    responses.add(
        responses.POST, "https://api.akahu.io/v1/refresh", status=200, json={}
    )
    trigger_akahu_refresh()

    call = responses.calls[0]
    assert call.request.headers["Authorization"] == "Bearer akahu-user"
    assert call.request.headers["X-Akahu-ID"] == "akahu-app"


@responses.activate
def test_get_akahu_balance_returns_current_balance():
    from modules.account_fetcher import get_akahu_balance

    responses.add(
        responses.GET,
        "https://api.akahu.io/v1/accounts/acc_123",
        json={"item": {"balance": {"current": 1234.56}}},
        status=200,
    )

    balance = get_akahu_balance("acc_123", "https://api.akahu.io/v1", {})
    assert balance == 1234.56


@responses.activate
def test_get_akahu_balance_returns_none_on_http_error():
    from modules.account_fetcher import get_akahu_balance

    responses.add(
        responses.GET,
        "https://api.akahu.io/v1/accounts/acc_123",
        json={"error": "not found"},
        status=404,
    )

    assert get_akahu_balance("acc_123", "https://api.akahu.io/v1", {}) is None


@responses.activate
def test_fetch_akahu_accounts_filters_to_active_and_renames_id():
    from modules.account_fetcher import fetch_akahu_accounts

    responses.add(
        responses.GET,
        "https://api.akahu.io/v1/accounts",  # config composes BASE + "/accounts"
        json={
            "items": [
                {
                    "_id": "acc_active",
                    "status": "ACTIVE",
                    "connection": {"name": "Kiwibank"},
                    "name": "Everyday",
                },
                {
                    "_id": "acc_inactive",
                    "status": "INACTIVE",
                    "connection": {"name": "ANZ"},
                    "name": "Old",
                },
            ]
        },
        status=200,
    )

    accounts = fetch_akahu_accounts()

    assert list(accounts.keys()) == ["acc_active"]
    assert accounts["acc_active"]["id"] == "acc_active"
    assert accounts["acc_active"]["connection"] == "Kiwibank"
    assert "_id" not in accounts["acc_active"]
