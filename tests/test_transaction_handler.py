"""Tests for the Decimal-parse error context added for issue #14.

These call the real `load_transactions_into_actual` with actualpy
dependencies mocked, so they verify the actual code path rather than a
helper mirror that could silently drift.
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
