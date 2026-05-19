"""Tests for sync_cli argument helpers."""


def test_set_env_override_ignores_missing_value(monkeypatch):
    from sync_cli import set_env_override

    monkeypatch.delenv("MAPPING_FILE", raising=False)

    set_env_override("MAPPING_FILE", None)

    assert "MAPPING_FILE" not in __import__("os").environ


def test_set_env_override_preserves_empty_string(monkeypatch):
    from sync_cli import set_env_override

    set_env_override("LOG_FILE", "")

    assert __import__("os").environ["LOG_FILE"] == ""
