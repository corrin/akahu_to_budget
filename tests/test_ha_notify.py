"""Tests for Home Assistant notifications via the Supervisor API."""

import requests


class _Response:
    def __init__(self, status=200):
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"{self.status}")


def _capture(monkeypatch, status=200):
    import modules.ha_notify as ha_notify

    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append((url, headers, json))
        return _Response(status)

    monkeypatch.setattr(ha_notify.requests, "post", fake_post)
    return ha_notify, calls


def test_alert_creates_persistent_notification(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    ha_notify, calls = _capture(monkeypatch)

    assert ha_notify.alert("Title", "Body") is True

    url, headers, payload = calls[0]
    assert url == "http://supervisor/core/api/services/persistent_notification/create"
    assert headers == {"Authorization": "Bearer tok"}
    assert payload == {
        "notification_id": "akahu_to_budget_sync",
        "title": "Title",
        "message": "Body",
    }


def test_alert_also_calls_notify_service(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    ha_notify, calls = _capture(monkeypatch)

    ha_notify.alert("Title", "Body", "notify.mobile_app_phone")

    assert calls[1][0] == "http://supervisor/core/api/services/notify/mobile_app_phone"
    assert calls[1][2] == {"title": "Title", "message": "Body"}


def test_clear_dismisses_notification(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    ha_notify, calls = _capture(monkeypatch)

    assert ha_notify.clear() is True
    assert calls[0][0].endswith("/persistent_notification/dismiss")


def test_missing_token_logs_error_and_returns_false(monkeypatch, caplog):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    ha_notify, calls = _capture(monkeypatch)

    assert ha_notify.alert("Title", "Body") is False
    assert calls == []
    assert "SUPERVISOR_TOKEN" in caplog.text


def test_http_error_returns_false(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    ha_notify, _ = _capture(monkeypatch, status=500)

    assert ha_notify.alert("Title", "Body") is False
