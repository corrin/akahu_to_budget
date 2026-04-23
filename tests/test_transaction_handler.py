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
