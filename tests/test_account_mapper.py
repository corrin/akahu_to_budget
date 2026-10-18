"""Tests for modules.account_mapper: OpenAI fallback and Actual link regeneration."""

from datetime import date, datetime, timezone
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


def test_save_mapping_failure_is_fatal(tmp_path):
    from modules.mapping_store import save_mapping

    with pytest.raises(IsADirectoryError):
        save_mapping(
            {
                "akahu_accounts": {},
                "actual_accounts": {},
                "ynab_accounts": {},
                "mapping": {},
            },
            mapping_file=tmp_path,
        )


def test_load_existing_mapping_missing_file_is_fatal_by_default(tmp_path):
    from modules.mapping_store import load_existing_mapping

    with pytest.raises(FileNotFoundError):
        load_existing_mapping(mapping_file=tmp_path / "missing.json")


def test_load_existing_mapping_can_generate_stub(tmp_path):
    from modules.mapping_store import load_existing_mapping

    mapping_file = tmp_path / "mapping.json"

    akahu_accounts, actual_accounts, ynab_accounts, mapping = load_existing_mapping(
        mapping_file=mapping_file,
        generate_stub=True,
    )

    assert mapping_file.exists()
    assert akahu_accounts == {}
    assert actual_accounts == {}
    assert ynab_accounts == {}
    assert mapping == {}


NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


def _linked(actual_id, akahu_id, name, offbudget=False, newest=None):
    return {
        "id": actual_id,
        "name": name,
        "akahu_id": akahu_id,
        "offbudget": offbudget,
        "newest_imported_date": newest,
    }


def _entry(akahu_id, actual_id, name, **extra):
    return {
        "akahu_id": akahu_id,
        "akahu_name": f"Akahu {name}",
        "account_type": "On Budget",
        "actual_account_id": actual_id,
        "actual_account_name": name,
        "actual_budget_id": "old-budget",
        "actual_matched_date": "2025-01-01T00:00:00",
        "actual_synced_datetime": "2026-07-25T17:30:36Z",
        **extra,
    }


AKAHU = {
    "acc_old": {"name": "Card"},
    "acc_new": {"name": "Card"},
    "acc_save": {"name": "Savings"},
}


def test_unchanged_link_keeps_watermark_and_matched_date():
    from modules.account_mapper import apply_actual_links

    mapping = {"acc_old": _entry("acc_old", "act-1", "Westpac CC")}
    updated, changes = apply_actual_links(
        mapping, [_linked("act-1", "acc_old", "Westpac CC")], AKAHU, "sync-id", NOW
    )

    entry = updated["acc_old"]
    assert entry["actual_synced_datetime"] == "2026-07-25T17:30:36Z"
    assert entry["actual_matched_date"] == "2025-01-01T00:00:00"
    assert entry["actual_budget_id"] == "sync-id"
    assert changes == []


def test_relink_moves_entry_and_carries_watermark():
    from modules.account_mapper import apply_actual_links

    mapping = {
        "acc_old": _entry("acc_old", "act-1", "Westpac CC", ynab_account_id="y1")
    }
    updated, changes = apply_actual_links(
        mapping, [_linked("act-1", "acc_new", "Westpac CC")], AKAHU, "sync-id", NOW
    )

    assert "actual_account_id" not in updated["acc_old"]
    assert updated["acc_old"]["ynab_account_id"] == "y1"
    assert updated["acc_new"]["actual_account_id"] == "act-1"
    assert updated["acc_new"]["actual_synced_datetime"] == "2026-07-25T17:30:36Z"
    assert updated["acc_new"]["akahu_name"] == "Card"
    assert any(level == logging.WARNING and "Relinked" in msg for level, msg in changes)


def test_unlinked_in_actual_strips_only_actual_fields():
    from modules.account_mapper import apply_actual_links

    mapping = {
        "acc_old": _entry("acc_old", "act-1", "Westpac CC", ynab_account_id="y1"),
        "acc_save": _entry("acc_save", "act-2", "Savings"),
    }
    updated, changes = apply_actual_links(
        mapping, [_linked("act-2", "acc_save", "Savings")], AKAHU, "sync-id", NOW
    )

    old = updated["acc_old"]
    assert "actual_account_id" not in old
    assert "actual_synced_datetime" not in old
    assert old["actual_do_not_map"] is True
    assert old["ynab_account_id"] == "y1"
    assert old["akahu_name"] == "Akahu Westpac CC"
    assert any("no longer sync" in msg for _, msg in changes)


def test_new_link_watermark_uses_newest_imported_date():
    from modules.account_mapper import apply_actual_links

    updated, changes = apply_actual_links(
        {}, [_linked("act-2", "acc_save", "Savings", newest=date(2026, 10, 1))],
        AKAHU, "sync-id", NOW,
    )

    assert updated["acc_save"]["actual_synced_datetime"] == "2026-10-01T00:00:00Z"
    assert updated["acc_save"]["actual_matched_date"] == "2026-10-18T09:00:00Z"
    assert any("Linked 'Savings'" in msg for _, msg in changes)


def test_new_link_without_history_looks_back_90_days():
    from modules.account_mapper import apply_actual_links

    updated, _ = apply_actual_links(
        {}, [_linked("act-2", "acc_save", "Savings")], AKAHU, "sync-id", NOW
    )

    assert updated["acc_save"]["actual_synced_datetime"] == "2026-07-20T09:00:00Z"


def test_account_type_follows_offbudget_and_reports_change():
    from modules.account_mapper import apply_actual_links

    mapping = {"acc_save": _entry("acc_save", "act-2", "Savings")}
    updated, changes = apply_actual_links(
        mapping, [_linked("act-2", "acc_save", "Savings", offbudget=True)],
        AKAHU, "sync-id", NOW,
    )

    assert updated["acc_save"]["account_type"] == "Tracking"
    assert any(level == logging.WARNING and "Tracking" in msg for level, msg in changes)


def test_link_missing_from_akahu_is_skipped_as_error_and_others_sync():
    from modules.account_mapper import apply_actual_links

    mapping = {"acc_gone": _entry("acc_gone", "act-1", "Westpac CC")}
    updated, changes = apply_actual_links(
        mapping,
        [
            _linked("act-1", "acc_gone", "Westpac CC"),
            _linked("act-2", "acc_save", "Savings"),
        ],
        AKAHU, "sync-id", NOW,
    )

    errors = [msg for level, msg in changes if level == logging.ERROR]
    assert len(errors) == 1 and "relink it" in errors[0]
    assert updated["acc_gone"]["actual_do_not_map"] is True
    assert updated["acc_save"]["actual_account_id"] == "act-2"
    assert not any("no longer sync" in msg for _, msg in changes)


def test_keep_account_type_when_ynab_owns_it():
    from modules.account_mapper import apply_actual_links

    mapping = {
        "acc_save": _entry("acc_save", "act-2", "Savings", ynab_account_id="y-2")
    }
    updated, changes = apply_actual_links(
        mapping, [_linked("act-2", "acc_save", "Savings", offbudget=True)],
        AKAHU, "sync-id", NOW, keep_account_type=True,
    )

    assert updated["acc_save"]["account_type"] == "On Budget"
    assert any("YNAB sync owns" in msg for _, msg in changes)


def test_keep_account_type_ignores_entries_not_linked_to_ynab():
    from modules.account_mapper import apply_actual_links

    mapping = {"acc_save": _entry("acc_save", "act-2", "Savings")}
    updated, _ = apply_actual_links(
        mapping, [_linked("act-2", "acc_save", "Savings", offbudget=True)],
        AKAHU, "sync-id", NOW, keep_account_type=True,
    )

    assert updated["acc_save"]["account_type"] == "Tracking"


def test_keep_account_type_still_sets_type_for_new_entries():
    from modules.account_mapper import apply_actual_links

    updated, _ = apply_actual_links(
        {}, [_linked("act-2", "acc_save", "Savings", offbudget=True)],
        AKAHU, "sync-id", NOW, keep_account_type=True,
    )

    assert updated["acc_save"]["account_type"] == "Tracking"


def test_two_actual_accounts_on_one_akahu_account_fails_loud():
    from modules.account_mapper import apply_actual_links

    with pytest.raises(RuntimeError, match="both linked"):
        apply_actual_links(
            {},
            [_linked("act-1", "acc_save", "A"), _linked("act-2", "acc_save", "B")],
            AKAHU, "sync-id", NOW,
        )


def test_actual_link_overrides_do_not_map():
    from modules.account_mapper import apply_actual_links

    mapping = {"acc_save": {"akahu_id": "acc_save", "actual_do_not_map": True}}
    updated, _ = apply_actual_links(
        mapping, [_linked("act-2", "acc_save", "Savings")], AKAHU, "sync-id", NOW
    )

    assert "actual_do_not_map" not in updated["acc_save"]
    assert updated["acc_save"]["actual_account_id"] == "act-2"


def test_input_mapping_is_not_mutated():
    from modules.account_mapper import apply_actual_links

    mapping = {"acc_old": _entry("acc_old", "act-1", "Westpac CC")}
    apply_actual_links(mapping, [], AKAHU, "sync-id", NOW)

    assert mapping["acc_old"]["actual_account_id"] == "act-1"


def test_unlinked_accounts_keep_do_not_map_quietly():
    from modules.account_mapper import apply_actual_links

    mapping = {
        "acc_save": {
            "akahu_id": "acc_save",
            "actual_do_not_map": True,
            "actual_matched_date": "2025-01-24T22:36:04",
        }
    }
    updated, changes = apply_actual_links(mapping, [], AKAHU, "sync-id", NOW)

    assert updated["acc_save"]["actual_do_not_map"] is True
    assert updated["acc_save"]["actual_matched_date"] == "2025-01-24T22:36:04"
    assert changes == []


def test_merge_removes_links_to_deleted_actual_and_ynab_accounts(monkeypatch):
    from modules.account_mapper import merge_and_update_mapping

    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    mapping = {
        "acc_1": {"akahu_id": "acc_1", "actual_account_id": "act_gone",
                  "actual_account_name": "Gone", "ynab_account_id": "ynab_keep"},
        "acc_2": {"akahu_id": "acc_2", "actual_account_id": "act_keep",
                  "ynab_account_id": "ynab_gone", "ynab_budget_id": "b1"},
    }
    akahu = {"acc_1": {"id": "acc_1"}, "acc_2": {"id": "acc_2"}}

    updated, _, _, _ = merge_and_update_mapping(
        mapping,
        akahu, {"act_keep": {"id": "act_keep"}}, {"ynab_keep": {"id": "ynab_keep"}},
        dict(akahu),
        {"act_gone": {"id": "act_gone"}, "act_keep": {"id": "act_keep"}},
        {"ynab_gone": {"id": "ynab_gone"}, "ynab_keep": {"id": "ynab_keep"}},
    )

    assert updated["acc_1"] == {"akahu_id": "acc_1", "ynab_account_id": "ynab_keep"}
    assert updated["acc_2"] == {"akahu_id": "acc_2", "actual_account_id": "act_keep"}


def test_broken_link_keeps_watermark_until_akahu_returns_it():
    from modules.account_mapper import apply_actual_links

    mapping = {"acc_gone": _entry("acc_gone", "act-1", "Westpac CC")}
    links = [_linked("act-1", "acc_gone", "Westpac CC")]

    broken, _ = apply_actual_links(mapping, links, AKAHU, "sync-id", NOW)
    assert broken["acc_gone"]["actual_do_not_map"] is True
    assert broken["acc_gone"]["actual_synced_datetime"] == "2026-07-25T17:30:36Z"

    back = {**AKAHU, "acc_gone": {"name": "Card"}}
    recovered, _ = apply_actual_links(broken, links, back, "sync-id", NOW)
    assert "actual_do_not_map" not in recovered["acc_gone"]
    assert recovered["acc_gone"]["actual_synced_datetime"] == "2026-07-25T17:30:36Z"
