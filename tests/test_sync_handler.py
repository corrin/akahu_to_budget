"""Integration tests for sync_to_ynab with Akahu + YNAB mocked.

sync_to_ab is intentionally out of scope here; actualpy has a large
surface that needs its own fixture set (phase 2).
"""

import pytest


@pytest.fixture(autouse=True)
def _env(full_env, reload_config):
    reload_config()


@pytest.fixture(autouse=True)
def _no_mapping_file_writes(mocker):
    """sync_to_ynab calls update_mapping_timestamps on success, which reads
    and rewrites the real akahu_budget_mapping.json. Block that in tests."""
    from modules import sync_handler

    mocker.patch.object(sync_handler, "update_mapping_timestamps")


def test_empty_mapping_uploads_nothing(mocker):
    from modules import sync_handler

    akahu_balance = mocker.patch.object(sync_handler, "get_akahu_balance")
    ynab_balance = mocker.patch.object(sync_handler, "get_ynab_balance")

    result = sync_handler.sync_to_ynab({})

    assert result == 0
    akahu_balance.assert_not_called()
    ynab_balance.assert_not_called()


def test_do_not_map_skips_account(mocker):
    from modules import sync_handler

    akahu_balance = mocker.patch.object(sync_handler, "get_akahu_balance")

    mapping = {
        "acc_dnt": {
            "akahu_name": "Joint Savings",
            "ynab_do_not_map": True,
            "ynab_budget_id": "bud",
            "ynab_account_id": "ynab_acc",
            "account_type": "Tracking",
        }
    }

    assert sync_handler.sync_to_ynab(mapping) == 0
    akahu_balance.assert_not_called()


def test_missing_ynab_ids_are_skipped_with_warning(mocker, caplog):
    import logging

    from modules import sync_handler

    mapping = {
        "acc_noynab": {
            "akahu_name": "Savings",
            "ynab_budget_id": None,
            "ynab_account_id": None,
            "account_type": "Tracking",
        }
    }

    with caplog.at_level(logging.WARNING):
        assert sync_handler.sync_to_ynab(mapping) == 0

    assert any(
        "Missing YNAB IDs" in rec.getMessage() for rec in caplog.records
    )


def test_tracking_account_matching_balances_creates_no_adjustment(mocker):
    from modules import sync_handler

    mocker.patch.object(sync_handler, "get_akahu_balance", return_value=100.00)
    # YNAB stores cents*10 (milliunits); 100.00 dollars == 100_000 milliunits.
    mocker.patch.object(sync_handler, "get_ynab_balance", return_value=100_000)
    adjust = mocker.patch.object(sync_handler, "create_adjustment_txn_ynab")

    mapping = {
        "acc_tracking": {
            "akahu_name": "Kiwisaver",
            "ynab_budget_id": "bud",
            "ynab_account_id": "ynab_acc",
            "ynab_account_name": "Kiwisaver YNAB",
            "account_type": "Tracking",
        }
    }

    assert sync_handler.sync_to_ynab(mapping) == 0
    adjust.assert_not_called()


def test_tracking_account_mismatched_balances_creates_adjustment(mocker):
    from modules import sync_handler

    # Akahu reports $100.50, YNAB still has $95.00 (95_000 milliunits).
    mocker.patch.object(sync_handler, "get_akahu_balance", return_value=100.50)
    mocker.patch.object(sync_handler, "get_ynab_balance", return_value=95_000)
    adjust = mocker.patch.object(sync_handler, "create_adjustment_txn_ynab")

    mapping = {
        "acc_tracking": {
            "akahu_name": "Kiwisaver",
            "ynab_budget_id": "bud",
            "ynab_account_id": "ynab_acc",
            "ynab_account_name": "Kiwisaver YNAB",
            "account_type": "Tracking",
        }
    }

    count = sync_handler.sync_to_ynab(mapping)

    assert count == 1
    adjust.assert_called_once()
    # Inspect the call: akahu_milliunits should be 100_500, ynab_milliunits 95_000
    call_args = adjust.call_args
    positional = call_args.args
    # Signature: (budget_id, account_id, akahu_milliunits, ynab_milliunits, endpoint, headers)
    assert positional[0] == "bud"
    assert positional[1] == "ynab_acc"
    assert positional[2] == 100_500
    assert positional[3] == 95_000
