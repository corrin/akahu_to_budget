"""Tests for modules.account_mapper focused on the OpenAI→fuzzy fallback."""

import logging

import pytest


@pytest.fixture(autouse=True)
def _env_for_mapper(full_env, reload_config):
    reload_config()


@pytest.fixture
def fake_akahu_account():
    return {"name": "Kiwibank Everyday", "connection": "Kiwibank"}


@pytest.fixture
def fake_targets():
    # Already has 'seq' as match_accounts assigns them before calling the suggester.
    return [
        {"id": "a1", "name": "Everyday", "seq": 1},
        {"id": "a2", "name": "Savings", "seq": 2},
    ]


def test_openai_failure_logs_warning_not_error(
    monkeypatch, caplog, fake_akahu_account, fake_targets
):
    """On OpenAI failure we warn and fall back to fuzzy — not ERROR."""
    import modules.account_mapper as m

    class _FailingOpenAI:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    raise RuntimeError("simulated 401")

    # account_mapper imports openai lazily inside the function; patch the
    # module-level openai that the lazy import will find.
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **_kwargs: _FailingOpenAI())

    with caplog.at_level(logging.WARNING):
        m.get_openai_match_suggestion(
            fake_akahu_account, fake_targets, {}, "actual_account_id"
        )

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "falling back to fuzzy match" in w.getMessage() for w in warnings
    )
    # The old code logged ERROR - guard against regression.
    assert not any(r.levelname == "ERROR" for r in caplog.records)


def test_openai_failure_returns_fuzzy_result(
    monkeypatch, fake_akahu_account, fake_targets
):
    import modules.account_mapper as m

    class _FailingOpenAI:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    raise RuntimeError("boom")

    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **_kwargs: _FailingOpenAI())

    result = m.get_openai_match_suggestion(
        fake_akahu_account, fake_targets, {}, "actual_account_id"
    )
    # Fuzzy returns either a seq or None; the important thing is it did not
    # propagate the OpenAI exception.
    assert result is None or isinstance(result, int)


def test_seq_to_acct_returns_account_for_existing_seq(fake_targets):
    from modules.account_mapper import seq_to_acct

    assert seq_to_acct(1, fake_targets)["id"] == "a1"
    assert seq_to_acct(2, fake_targets)["id"] == "a2"


def test_seq_to_acct_returns_none_for_unknown_seq(fake_targets):
    from modules.account_mapper import seq_to_acct

    assert seq_to_acct(99, fake_targets) is None


def test_validate_user_input_accepts_zero_as_do_not_map():
    from modules.account_mapper import validate_user_input

    assert validate_user_input("0", [], {}, "actual_account_id") == 0


def test_validate_user_input_rejects_non_numeric():
    from modules.account_mapper import validate_user_input

    assert validate_user_input("abc", [], {}, "actual_account_id") is None
