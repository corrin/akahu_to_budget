"""Tests for load_transactions_into_actual.

These drive the real function with actualpy dependencies mocked, so
behaviour is verified against the code path rather than a helper
that might silently drift.
"""

import decimal

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _env(full_env, reload_config):
    reload_config()


def _run_with_one_transaction(mocker, raw_amount):
    """Drive load_transactions_into_actual with a single txn whose amount is raw_amount.

    Everything actualpy-related is mocked to a happy path so only the
    amount-parse branch is exercised.
    """
    from modules import transaction_handler

    mocker.patch.object(
        transaction_handler, "get_cached_names", return_value=({}, {})
    )
    mocker.patch.object(transaction_handler, "get_ruleset", return_value=None)

    fake_actual = mocker.MagicMock()
    fake_actual.session = mocker.MagicMock()

    df = pd.DataFrame(
        [
            {
                "_id": "trans_xyz789",
                "amount": raw_amount,
                "date": "2025-05-26T04:00:00Z",
                "description": "Weird",
            }
        ]
    )

    mapping_entry = {"actual_account_id": "actual-acc-1"}
    transaction_handler.load_transactions_into_actual(df, mapping_entry, fake_actual)


@pytest.mark.parametrize(
    "bad_value",
    # Note: None isn't listed because pandas coerces it to NaN in a single-
    # row DataFrame, and Decimal(NaN) parses successfully. In production a
    # None amount from Akahu may survive as None in an object-dtype column
    # (mixed with strings from other rows), but that path is hard to simulate
    # reliably at this fidelity. The wrapper still catches TypeError in case
    # it does.
    ["abc", "", {"x": 1}, [1, 2, 3]],
)
def test_bad_amount_raises_runtime_error_with_context(mocker, bad_value):
    with pytest.raises(RuntimeError) as excinfo:
        _run_with_one_transaction(mocker, bad_value)

    msg = str(excinfo.value)
    assert "trans_xyz789" in msg
    assert repr(bad_value) in msg or str(bad_value) in msg
    assert excinfo.value.__cause__ is not None, (
        "original exception should be chained via `raise ... from e`"
    )
    cause = excinfo.value.__cause__
    assert isinstance(cause, (decimal.InvalidOperation, TypeError, ValueError))


def test_error_distinguishes_empty_string_from_garbage(mocker):
    """Error messages should make different bad inputs distinguishable."""
    with pytest.raises(RuntimeError) as exc_empty:
        _run_with_one_transaction(mocker, "")
    with pytest.raises(RuntimeError) as exc_abc:
        _run_with_one_transaction(mocker, "abc")

    assert "''" in str(exc_empty.value)
    assert "'abc'" in str(exc_abc.value)
    assert str(exc_empty.value) != str(exc_abc.value)


def test_actual_import_prefers_merchant_name_for_payee(mocker):
    from modules import transaction_handler

    mocker.patch.object(
        transaction_handler, "get_cached_names", return_value=({}, {})
    )
    mocker.patch.object(transaction_handler, "get_ruleset", return_value=None)

    fake_txn = mocker.MagicMock()
    fake_txn.changed.side_effect = [True, True]
    reconcile = mocker.patch.object(
        transaction_handler, "reconcile_transaction", return_value=fake_txn
    )

    fake_actual = mocker.MagicMock()
    fake_actual.session = mocker.MagicMock()
    df = pd.DataFrame(
        [
            {
                "_id": "trans_merchant",
                "amount": -7.5,
                "date": "2025-05-26T04:00:00Z",
                "description": "EFTPOS PURCHASE 1234",
                "merchant": {"name": "Coffee Supreme"},
                "type": "debit",
                "category": {"name": "Food and Drink"},
            }
        ]
    )

    transaction_handler.load_transactions_into_actual(
        df, {"actual_account_id": "actual-acc-1"}, fake_actual
    )

    assert reconcile.call_args.kwargs["payee"] == "Coffee Supreme"
    assert reconcile.call_args.kwargs["imported_payee"] == "Coffee Supreme"


def test_actual_import_falls_back_to_description_and_formats_notes(mocker):
    from modules import transaction_handler

    mocker.patch.object(
        transaction_handler, "get_cached_names", return_value=({}, {})
    )
    mocker.patch.object(transaction_handler, "get_ruleset", return_value=None)

    fake_txn = mocker.MagicMock()
    fake_txn.changed.side_effect = [True, True]
    reconcile = mocker.patch.object(
        transaction_handler, "reconcile_transaction", return_value=fake_txn
    )

    fake_actual = mocker.MagicMock()
    fake_actual.session = mocker.MagicMock()
    df = pd.DataFrame(
        [
            {
                "_id": "trans_description",
                "amount": -12.0,
                "date": "2025-05-26T04:00:00Z",
                "description": "Countdown Auckland",
                "merchant": None,
                "type": "debit",
                "category": {"name": "Groceries"},
            }
        ]
    )

    transaction_handler.load_transactions_into_actual(
        df, {"actual_account_id": "actual-acc-1"}, fake_actual
    )

    assert reconcile.call_args.kwargs["payee"] == "Countdown Auckland"
    assert reconcile.call_args.kwargs["notes"] == (
        "debit | Groceries | Countdown Auckland"
    )


def test_actual_notes_omit_missing_context_without_empty_separators():
    from modules.transaction_handler import format_transaction_notes

    assert format_transaction_notes(
        {
            "type": "",
            "category": {},
            "description": "ATM Withdrawal",
        }
    ) == "ATM Withdrawal"


# --- ruleset-driven transfer payees should materialise the mirror transaction ---


def _run_with_ruleset(
    mocker,
    rule_sets_payee_to,
    new_payee_transfer_acct,
    old_payee_id=None,
    set_payee_side_effect=None,
):
    """Drive load_transactions_into_actual with a single valid transaction
    and a ruleset that reassigns the payee_id as specified.

    Returns the patched set_transaction_payee and get_payee so the test can
    assert on call arguments.
    """
    from modules import transaction_handler

    mocker.patch.object(
        transaction_handler, "get_cached_names", return_value=({}, {})
    )

    # Build the mock transaction that reconcile_transaction will return.
    fake_txn = mocker.MagicMock()
    fake_txn.payee_id = old_payee_id
    fake_txn.changed.return_value = True
    mocker.patch.object(
        transaction_handler, "reconcile_transaction", return_value=fake_txn
    )

    # Build a ruleset whose .run() mutates the transaction's payee_id.
    fake_ruleset = mocker.MagicMock()

    def _rules_mutate(txn):
        txn.payee_id = rule_sets_payee_to

    fake_ruleset.run.side_effect = _rules_mutate
    mocker.patch.object(
        transaction_handler, "get_ruleset", return_value=fake_ruleset
    )

    # The new payee looked up via get_payee - configured with transfer_acct
    # either truthy (== transfer payee) or falsy.
    fake_new_payee = mocker.MagicMock()
    fake_new_payee.transfer_acct = new_payee_transfer_acct
    get_payee_mock = mocker.patch.object(
        transaction_handler, "get_payee", return_value=fake_new_payee
    )

    set_payee_mock = mocker.patch.object(
        transaction_handler,
        "set_transaction_payee",
        side_effect=set_payee_side_effect,
    )

    fake_actual = mocker.MagicMock()

    df = pd.DataFrame(
        [
            {
                "_id": "trans_xfer",
                "amount": -12.50,
                "date": "2025-02-10T04:00:00Z",
                "description": "New World Ne",
            }
        ]
    )
    mapping_entry = {"actual_account_id": "actual-source-acc"}
    transaction_handler.load_transactions_into_actual(df, mapping_entry, fake_actual)

    return {
        "get_payee": get_payee_mock,
        "set_transaction_payee": set_payee_mock,
        "fake_txn": fake_txn,
        "fake_new_payee": fake_new_payee,
    }


def test_rule_setting_transfer_payee_triggers_set_transaction_payee(mocker):
    """When a rule reassigns payee to an account-backed ('transfer')
    payee, the change must be routed through set_transaction_payee
    so the mirror transaction on the target account is created."""
    result = _run_with_ruleset(
        mocker,
        rule_sets_payee_to="new-transfer-payee-id",
        new_payee_transfer_acct="target-account-uuid",
        old_payee_id=None,
    )

    result["set_transaction_payee"].assert_called_once()
    call_args = result["set_transaction_payee"].call_args
    # set_transaction_payee(session, transaction, payee)
    assert call_args.args[1] is result["fake_txn"]
    assert call_args.args[2] is result["fake_new_payee"]


def test_payee_id_is_reverted_before_calling_set_transaction_payee(mocker):
    """set_transaction_payee uses transaction.payee_id to detect the OLD
    payee for 'delete old transfer mirror' semantics. We must revert the
    rule's direct mutation before calling, otherwise the function sees the
    new payee_id as both the old and the new state."""
    captured_payee_id_at_call = []

    def _spy(_session, transaction, _payee):
        captured_payee_id_at_call.append(transaction.payee_id)

    _run_with_ruleset(
        mocker,
        rule_sets_payee_to="new-transfer-payee-id",
        new_payee_transfer_acct="target-account-uuid",
        old_payee_id="original-payee-id",
        set_payee_side_effect=_spy,
    )

    assert captured_payee_id_at_call == ["original-payee-id"], (
        "payee_id must be reverted to pre-rules value before "
        "set_transaction_payee runs"
    )


def test_rule_setting_non_transfer_payee_does_not_call_set_transaction_payee(mocker):
    """When rules change the payee to a normal (non-account-backed) payee,
    we must NOT call set_transaction_payee - that would be wasted work and
    could subtly alter other state."""
    result = _run_with_ruleset(
        mocker,
        rule_sets_payee_to="some-normal-payee-id",
        new_payee_transfer_acct=None,  # not a transfer payee
    )
    result["set_transaction_payee"].assert_not_called()


def test_rule_not_changing_payee_does_not_call_set_transaction_payee(mocker):
    """If rules leave the payee alone, we must not call set_transaction_payee."""
    result = _run_with_ruleset(
        mocker,
        rule_sets_payee_to=None,  # no change from old_payee_id=None
        new_payee_transfer_acct="whatever",
        old_payee_id=None,
    )
    result["set_transaction_payee"].assert_not_called()


def _fake_actual_txn(mocker, txn_id, financial_id, changed=False):
    txn = mocker.MagicMock()
    txn.id = txn_id
    txn.financial_id = financial_id
    txn.changed.return_value = changed
    return txn


def _akahu_row(txn_id, date, amount=-10.0, description="TEST MERCHANT"):
    return {"_id": txn_id, "amount": amount, "date": date, "description": description}


def _patch_actual_deps(mocker, existing=(), reconcile_results=()):
    from modules import transaction_handler

    mocker.patch.object(
        transaction_handler, "get_cached_names", return_value=({}, {})
    )
    mocker.patch.object(transaction_handler, "get_ruleset", return_value=None)
    get_txns = mocker.patch.object(
        transaction_handler, "get_transactions", return_value=list(existing)
    )
    # Snapshot already_matched at call time; the list is mutated afterwards.
    seen_already_matched = []

    def reconcile(*args, **kwargs):
        seen_already_matched.append([t.id for t in kwargs["already_matched"]])
        return reconcile_results[len(seen_already_matched) - 1]

    mocker.patch.object(
        transaction_handler, "reconcile_transaction", side_effect=reconcile
    )
    fake_actual = mocker.MagicMock()
    return transaction_handler, fake_actual, get_txns, seen_already_matched


def test_imported_transactions_are_claimed_before_matching(mocker):
    """Every Actual txn with a financial_id is claimed up front, in or out of the batch.

    Akahu returns newest first, so without this the newer txn fuzzy-matches the
    older one's Actual record before the older Akahu txn claims it by
    imported_id. Txns from outside the batch must be claimed too. Manual
    entries (no financial_id) stay available for fuzzy matching.
    """
    older_in_actual = _fake_actual_txn(mocker, "actual-older", "akahu-older")
    outside_batch = _fake_actual_txn(mocker, "actual-other", "akahu-not-in-batch")
    manual_entry = _fake_actual_txn(mocker, "actual-manual", None)
    new_txn = _fake_actual_txn(mocker, "actual-new", "akahu-newer", changed=True)
    handler, fake_actual, get_txns, seen = _patch_actual_deps(
        mocker,
        existing=[older_in_actual, outside_batch, manual_entry],
        reconcile_results=[new_txn, older_in_actual],
    )
    df = pd.DataFrame([
        _akahu_row("akahu-newer", "2025-01-15T00:00:00Z"),
        _akahu_row("akahu-older", "2025-01-10T00:00:00Z"),
    ])

    imported = handler.load_transactions_into_actual(
        df, {"actual_account_id": "actual-acc-1"}, fake_actual
    )

    assert seen[0] == ["actual-older", "actual-other"]
    assert imported == 1
    kwargs = get_txns.call_args.kwargs
    assert kwargs["account"] == "actual-acc-1"
    assert str(kwargs["start_date"]) == "2025-01-03"
    assert str(kwargs["end_date"]) == "2025-01-23"


def test_matched_existing_transaction_is_added_to_already_matched(mocker):
    """A fuzzy-matched existing txn can't absorb a second Akahu txn in the same run."""
    manual_entry = _fake_actual_txn(mocker, "actual-manual", None)
    created = _fake_actual_txn(mocker, "actual-created", "akahu-2", changed=True)
    handler, fake_actual, _, seen = _patch_actual_deps(
        mocker, reconcile_results=[manual_entry, created]
    )
    df = pd.DataFrame([
        _akahu_row("akahu-1", "2025-01-15T00:00:00Z"),
        _akahu_row("akahu-2", "2025-01-15T01:00:00Z"),
    ])

    imported = handler.load_transactions_into_actual(
        df, {"actual_account_id": "actual-acc-1"}, fake_actual
    )

    assert seen == [[], ["actual-manual"]]
    assert imported == 1


def test_fuzzy_match_to_other_akahu_transaction_logs_warning(mocker, caplog):
    other = _fake_actual_txn(mocker, "actual-other", "akahu-out-of-window")
    handler, fake_actual, _, _ = _patch_actual_deps(
        mocker, reconcile_results=[other]
    )
    df = pd.DataFrame([_akahu_row("akahu-new", "2025-01-15T00:00:00Z")])

    with caplog.at_level("WARNING"):
        handler.load_transactions_into_actual(
            df, {"actual_account_id": "actual-acc-1"}, fake_actual
        )

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "akahu-new" in warnings[0].getMessage()
    assert "akahu-out-of-window" in warnings[0].getMessage()


def test_id_match_does_not_log_fuzzy_warning(mocker, caplog):
    own = _fake_actual_txn(mocker, "actual-own", "akahu-1")
    handler, fake_actual, _, _ = _patch_actual_deps(
        mocker, existing=[own], reconcile_results=[own]
    )
    df = pd.DataFrame([_akahu_row("akahu-1", "2025-01-15T00:00:00Z")])

    with caplog.at_level("INFO"):
        handler.load_transactions_into_actual(
            df, {"actual_account_id": "actual-acc-1"}, fake_actual
        )

    assert not any("fuzzy" in r.getMessage() for r in caplog.records)


def test_akahu_fetch_starts_14_days_before_last_sync(mocker):
    from modules import transaction_handler

    response = mocker.MagicMock()
    response.json.return_value = {"items": []}
    get = mocker.patch.object(transaction_handler.requests, "get", return_value=response)

    transaction_handler.get_all_akahu(
        "acc_1", "https://api.akahu.io/v1", {}, "2025-02-01T00:00:00Z"
    )

    assert get.call_args.kwargs["params"]["start"] == "2025-01-18T00:00:00Z"
