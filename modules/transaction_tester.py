"""Module for testing transaction operations."""

import logging
import time
import pandas as pd

from modules.config import RUN_SYNC_TO_AB

if RUN_SYNC_TO_AB:
    from actual.queries import get_transactions

# Test transaction template - matches Akahu API format
TEST_TRANSACTION_TEMPLATE = {
    "_account": "acc_test123",
    "_connection": "conn_test123",
    "created_at": "2024-01-01T00:00:00.000Z",
    "date": "2024-01-01T00:00:00.000Z",
    "description": "Test Transaction",
    "amount": 0.00,
    "balance": 1000.00,
    "type": "DEBIT",
}


def run_transaction_tests(actual_client, mapping_list, env_vars):
    """Run comprehensive transaction tests."""
    logger = logging.getLogger(__name__)
    logger.info("=== Starting Transaction Tests ===")

    # Get first mapping entry that's not configured to be skipped
    test_mapping = None
    logger.info("Looking for valid account mapping...")
    logger.debug("Scanning mapping list: %s", list(mapping_list.keys()))

    for mapping in mapping_list.values():
        logger.debug(
            "Checking mapping: %s",
            {
                "akahu_id": mapping.get("akahu_id"),
                "actual_account_id": mapping.get("actual_account_id"),
                "do_not_map": mapping.get("actual_do_not_map"),
            },
        )
        if not mapping.get("actual_do_not_map") and mapping.get("actual_account_id"):
            test_mapping = mapping
            logger.info(
                "Found valid mapping - Akahu ID: %s, Actual ID: %s",
                mapping.get("akahu_id"),
                mapping.get("actual_account_id"),
            )
            break

    if not test_mapping:
        logging.error("No valid account mapping found!")
        raise ValueError("No valid account mapping found")

    run_initial_transaction_test(actual_client, test_mapping)
    run_duplicate_transaction_test(actual_client, test_mapping)

    if test_mapping.get("ynab_account_id"):
        run_ynab_integration_test(actual_client, test_mapping, env_vars)
    else:
        logging.info("No YNAB mapping found - skipping YNAB test")

    run_transaction_update_test(actual_client, test_mapping)

    logging.info("\n=== All Transaction Tests Completed ===")
    return {
        "status": "completed",
        "message": "All transaction tests completed without errors",
    }


def run_initial_transaction_test(actual_client, test_mapping):
    """Test creating an initial transaction with SQL verification."""
    from .transaction_handler import load_transactions_into_actual
    from sqlmodel import select
    from actual.database import Transactions, Accounts

    logger = logging.getLogger(__name__)
    logger.info("\n=== Test 1: Creating Initial Transaction ===")

    # Generate unique ID using timestamp
    test_id = f"test_txn_{int(time.time())}"
    logger.debug("Generated test transaction ID: %s", test_id)

    # Pre-check using SQL
    query = select(Transactions).where(Transactions.financial_id == test_id)
    logger.debug("Executing pre-check SQL query: %s", query)
    existing = actual_client.session.exec(query).first()
    if existing:
        logger.error(
            "Pre-check: Found existing transaction: %s",
            {
                "id": existing.id,
                "financial_id": existing.financial_id,
                "amount": existing.amount,
                "imported_description": existing.imported_description,
            },
        )
        raise Exception("Test failed: Transaction exists before creation")
    logger.info("Pre-check: Verified transaction does not exist")

    # Create test transaction
    test_amount = 10.00
    test_date = "2024-01-01T00:00:00Z"
    test_desc = "Test Transaction 1"

    logger.debug(
        "Creating test transaction with properties: %s",
        {
            "amount": test_amount,
            "date": test_date,
            "imported_description": test_desc,
            "expected_amount_cents": int(test_amount * -100),
        },
    )

    # Create test transaction from template
    test_data = TEST_TRANSACTION_TEMPLATE.copy()
    test_data.update(
        {
            "_id": test_id,
            "date": test_date,
            "description": test_desc,
            "amount": test_amount,
        }
    )
    test_txn = pd.DataFrame([test_data])
    logger.debug("Created DataFrame: %s", test_txn.to_dict("records"))

    # Use API to create transaction
    load_transactions_into_actual(test_txn, test_mapping, actual_client)
    logging.info("Initial transaction creation completed")

    # Comprehensive SQL verification
    query = (
        select(Transactions)
        .join(Accounts)
        .where(
            Transactions.financial_id == test_id,
            Transactions.amount
            == int(test_amount * -100),  # Actual stores amounts in cents (negated)
            Transactions.acct == test_mapping["actual_account_id"],
        )
    )
    logger.debug("Executing verification SQL query: %s", query)
    created_txn = actual_client.session.exec(query).first()

    if not created_txn:
        logger.error("Failed to find created transaction with ID: %s", test_id)
        raise Exception("Test failed: Transaction was not created successfully")

    logger.debug(
        "Found created transaction: %s",
        {
            "id": created_txn.id,
            "financial_id": created_txn.financial_id,
            "amount": created_txn.amount,
            "amount_dollars": created_txn.amount / 100,
            "imported_description": created_txn.imported_description,
            "account_id": created_txn.acct,
        },
    )

    # Verify all properties
    expected_amount_cents = int(test_amount * -100)
    if created_txn.amount != expected_amount_cents:
        logger.error(
            "Amount mismatch: expected %d cents, got %d cents",
            expected_amount_cents,
            created_txn.amount,
        )
        raise Exception(
            f"Test failed: Amount mismatch. Expected {test_amount * -1}, got {created_txn.amount/100}"
        )

    if created_txn.acct != test_mapping["actual_account_id"]:
        logger.error(
            "Account mismatch: expected %s, got %s",
            test_mapping["actual_account_id"],
            created_txn.acct,
        )
        raise Exception("Test failed: Account ID mismatch")

    logger.info(
        "Post-creation check: Successfully verified transaction with all properties"
    )

    # Cleanup - mark transaction as deleted
    logger.debug("Marking test transaction as deleted (tombstone=1)")
    created_txn.tombstone = 1
    actual_client.session.add(created_txn)
    actual_client.session.commit()
    logger.debug("Cleanup completed for transaction ID: %s", test_id)


def run_duplicate_transaction_test(actual_client, test_mapping):
    """Test handling of duplicate transactions with SQL verification."""
    from .transaction_handler import load_transactions_into_actual
    from sqlmodel import select, func
    from actual.database import Transactions

    logger = logging.getLogger(__name__)
    logger.info("\n=== Test 2: Testing Duplicate Handling ===")

    # Generate unique ID using timestamp
    test_id = f"test_txn_{int(time.time())}"
    test_amount = 10.00
    logger.debug("Generated test data: ID=%s, amount=%.2f", test_id, test_amount)

    # Create test transaction
    # Create test transaction from template
    test_data = TEST_TRANSACTION_TEMPLATE.copy()
    test_data.update(
        {"_id": test_id, "description": "Test Transaction 1", "amount": test_amount}
    )
    test_txn = pd.DataFrame([test_data])
    logger.debug(
        "Created initial test transaction DataFrame: %s", test_txn.to_dict("records")
    )

    # Create first instance using API
    logger.debug("Attempting to create first instance of transaction")
    load_transactions_into_actual(test_txn, test_mapping, actual_client)
    logger.info("First instance creation completed")

    # SQL verification of first instance
    query = select(Transactions).where(
        Transactions.financial_id == test_id, Transactions.tombstone == 0
    )
    logger.debug("Executing verification query for first instance: %s", query)
    original_txn = actual_client.session.exec(query).first()
    if not original_txn:
        logger.error("Failed to find original transaction with ID: %s", test_id)
        raise Exception(
            "Test failed: Original transaction missing before duplicate test"
        )

    # Store original properties for comparison
    original_id = original_txn.id
    original_amount = original_txn.amount
    original_timestamp = original_txn.sort_order
    logger.debug(
        "Original transaction properties: %s",
        {
            "id": original_id,
            "amount": original_amount,
            "amount_dollars": original_amount / 100,
            "timestamp": original_timestamp,
            "imported_description": original_txn.imported_description,
        },
    )
    logger.info("Pre-duplicate check: Original transaction exists")

    # Attempt duplicate creation using API
    logger.debug("Attempting to create duplicate transaction with same ID: %s", test_id)
    load_transactions_into_actual(test_txn, test_mapping, actual_client)
    logger.info("Duplicate transaction attempt completed")

    # Comprehensive SQL verification
    query = (
        select(Transactions)
        .where(Transactions.financial_id == test_id, Transactions.tombstone == 0)
        .order_by(Transactions.sort_order.desc())
    )
    logger.debug("Executing post-duplicate verification query: %s", query)
    transactions = actual_client.session.exec(query).all()

    # Verify count
    transaction_count = len(transactions)
    logger.debug("Found %d transaction(s) with ID %s", transaction_count, test_id)
    if transaction_count != 1:
        logger.error("Unexpected number of transactions: %d", transaction_count)
        raise Exception(
            f"Test failed: Found {transaction_count} instances of transaction"
        )

    # Verify it's the same transaction
    current_txn = transactions[0]
    logger.debug(
        "Current transaction properties: %s",
        {
            "id": current_txn.id,
            "amount": current_txn.amount,
            "amount_dollars": current_txn.amount / 100,
            "timestamp": current_txn.sort_order,
            "imported_description": current_txn.imported_description,
        },
    )

    if current_txn.id != original_id:
        logger.error(
            "Transaction ID mismatch: expected %s, got %s", original_id, current_txn.id
        )
        raise Exception("Test failed: Transaction ID changed after duplicate attempt")
    if current_txn.amount != original_amount:
        logger.error(
            "Amount mismatch: expected %d cents, got %d cents",
            original_amount,
            current_txn.amount,
        )
        raise Exception(
            "Test failed: Transaction amount changed after duplicate attempt"
        )
    if current_txn.sort_order != original_timestamp:
        logger.error(
            "Timestamp mismatch: expected %s, got %s",
            original_timestamp,
            current_txn.sort_order,
        )
        raise Exception(
            "Test failed: Transaction timestamp changed after duplicate attempt"
        )

    logger.info(
        "Post-duplicate check: Verified only one transaction exists with unchanged properties"
    )

    # Cleanup
    logger.debug("Marking test transaction as deleted (tombstone=1)")
    current_txn.tombstone = 1
    actual_client.session.add(current_txn)
    actual_client.session.commit()
    logger.debug("Cleanup completed for transaction ID: %s", test_id)


def run_ynab_integration_test(actual_client, test_mapping, env_vars):
    """Test YNAB integration."""
    from .transaction_handler import (
        load_transactions_into_actual,
        clean_txn_for_ynab,
        load_transactions_into_ynab,
    )

    logger = logging.getLogger(__name__)
    logger.info("\n=== Test 3: Testing YNAB Integration ===")
    logger.info("YNAB mapping found - Account ID: %s", test_mapping["ynab_account_id"])
    logger.debug(
        "YNAB configuration: %s",
        {
            "account_id": test_mapping["ynab_account_id"],
            "budget_id": test_mapping["ynab_budget_id"],
            "endpoint": env_vars["ynab_endpoint"],
        },
    )

    # Generate unique ID using timestamp
    test_id = f"test_txn_{int(time.time())}"
    logger.debug("Generated test transaction ID: %s", test_id)

    # Create test transaction
    test_amount = 15.00
    # Create test transaction from template
    test_data = TEST_TRANSACTION_TEMPLATE.copy()
    test_data.update(
        {"_id": test_id, "description": "Test Transaction YNAB", "amount": test_amount}
    )
    test_txn = pd.DataFrame([test_data])
    logger.debug("Created test transaction DataFrame: %s", test_txn.to_dict("records"))

    # Create in Actual first
    logger.debug("Creating transaction in Actual")
    load_transactions_into_actual(test_txn, test_mapping, actual_client)
    logger.info("Transaction created in Actual")

    # First verify transaction doesn't exist in YNAB
    logger.debug("Checking if transaction already exists in YNAB")
    import_id = f"AKAHU:{test_id}"
    logger.debug("YNAB import_id to check: %s", import_id)

    # Use YNAB API to check for existing transaction
    check_url = f"{env_vars['ynab_endpoint']}budgets/{test_mapping['ynab_budget_id']}/transactions"
    logger.debug("Checking YNAB transactions at: %s", check_url)

    from .transaction_handler import get_ynab_transactions

    existing_transactions = get_ynab_transactions(
        test_mapping["ynab_budget_id"],
        env_vars["ynab_endpoint"],
        env_vars["ynab_headers"],
    )
    logger.debug("Retrieved %d transactions from YNAB", len(existing_transactions))

    # Check for our test transaction
    existing_transaction = next(
        (t for t in existing_transactions if t.get("import_id") == import_id), None
    )

    if existing_transaction:
        logger.debug("Found existing transaction in YNAB: %s", existing_transaction)
        logger.info("Transaction already exists in YNAB - will be handled as duplicate")
    else:
        logger.info("Verified transaction does not exist in YNAB")

    # Now proceed with cleaning and sending to YNAB
    logger.debug("Cleaning transaction for YNAB sync")
    cleaned_txn = clean_txn_for_ynab(test_txn, test_mapping["ynab_account_id"])
    logger.debug("Cleaned transaction: %s", cleaned_txn.to_dict("records"))
    logger.debug(
        "Sending transaction to YNAB with config: %s",
        {
            "budget_id": test_mapping["ynab_budget_id"],
            "account_id": test_mapping["ynab_account_id"],
        },
    )
    ynab_response = load_transactions_into_ynab(
        cleaned_txn,
        test_mapping["ynab_budget_id"],
        test_mapping["ynab_account_id"],
        env_vars["ynab_endpoint"],
        env_vars["ynab_headers"],
    )

    logger.debug("Received YNAB response: %s", ynab_response)

    if ynab_response and "data" in ynab_response:
        transactions = ynab_response["data"].get("transactions", [])
        duplicates = ynab_response["data"].get("duplicate_import_ids", [])

        logger.debug(
            "YNAB response analysis: %s",
            {
                "transactions_count": len(transactions),
                "duplicates_count": len(duplicates),
                "transaction_ids": (
                    [t.get("id") for t in transactions] if transactions else []
                ),
                "duplicate_ids": duplicates,
            },
        )

        if len(transactions) > 0:
            logger.info("Post-YNAB check: Transaction successfully created in YNAB")
            logger.debug("Created YNAB transaction details: %s", transactions[0])
        elif len(duplicates) > 0:
            logger.info(
                "Post-YNAB check: Transaction already exists in YNAB (duplicate)"
            )
            logger.debug("Duplicate import IDs: %s", duplicates)
        else:
            logger.error("YNAB response contains no transactions or duplicates")
            raise Exception(
                "Test failed: YNAB response indicates no transaction created or found"
            )
    else:
        logger.error("Invalid YNAB response format: %s", ynab_response)
        raise Exception("Test failed: Invalid YNAB response format")


def run_transaction_update_test(actual_client, test_mapping):
    """Test transaction update functionality with SQL verification."""
    from .transaction_handler import load_transactions_into_actual
    from sqlmodel import select
    from actual.database import Transactions, Accounts

    logger = logging.getLogger(__name__)
    logger.info("\n=== Test 4: Testing Transaction Updates ===")

    # Generate unique ID for update test
    update_id = f"test_txn_update_{int(time.time())}"
    logger.debug("Generated update test ID: %s", update_id)

    # Pre-check using SQL
    query = select(Transactions).where(
        Transactions.financial_id == update_id, Transactions.tombstone == 0
    )
    logger.debug("Executing pre-check SQL query: %s", query)
    existing = actual_client.session.exec(query).first()
    if existing:
        logger.error("Found existing transaction with ID: %s", update_id)
        raise Exception("Test failed: Update test transaction already exists")
    logger.info("Pre-check: Verified update test transaction does not exist")

    # Create initial transaction
    initial_amount = 20.00
    initial_desc = "Test Transaction 2 - Original"
    logger.debug(
        "Creating initial transaction with properties: %s",
        {
            "id": update_id,
            "amount": initial_amount,
            "imported_description": initial_desc,
            "expected_amount_cents": int(initial_amount * -100),
        },
    )

    # Create test transaction from template
    test_data = TEST_TRANSACTION_TEMPLATE.copy()
    test_data.update(
        {"_id": update_id, "description": initial_desc, "amount": initial_amount}
    )
    update_txn = pd.DataFrame([test_data])
    logger.debug("Created initial DataFrame: %s", update_txn.to_dict("records"))

    # Use API to create transaction
    logger.debug("Creating initial transaction in Actual")
    load_transactions_into_actual(update_txn, test_mapping, actual_client)
    logger.info("Initial transaction creation completed")

    # SQL verification of initial transaction
    query = (
        select(Transactions)
        .join(Accounts)
        .where(
            Transactions.financial_id == update_id,
            Transactions.amount == int(initial_amount * -100),
            Transactions.acct == test_mapping["actual_account_id"],
            Transactions.tombstone == 0,
        )
    )
    logger.debug("Executing initial verification SQL query: %s", query)
    initial_txn = actual_client.session.exec(query).first()

    if not initial_txn:
        logger.error("Failed to find initial transaction with ID: %s", update_id)
        raise Exception("Test failed: Initial transaction was not created")

    logger.debug(
        "Initial transaction state: %s",
        {
            "id": initial_txn.id,
            "amount": initial_txn.amount,
            "amount_dollars": initial_txn.amount / 100,
            "imported_description": initial_txn.imported_description,
            "account_id": initial_txn.acct,
        },
    )

    expected_amount_cents = int(initial_amount * -100)
    if initial_txn.amount != expected_amount_cents:
        logger.error(
            "Initial amount mismatch: expected %d cents, got %d cents",
            expected_amount_cents,
            initial_txn.amount,
        )
        raise Exception(
            f"Test failed: Initial amount incorrect. Expected {initial_amount * -1}, got {initial_txn.amount/100}"
        )
    if initial_txn.imported_description != initial_desc:
        logger.error(
            "Initial description mismatch: expected '%s', got '%s'",
            initial_desc,
            initial_txn.imported_description,
        )
        raise Exception("Test failed: Initial description incorrect")
    logger.info("Post-creation check: Successfully verified initial transaction")

    # Store original ID for comparison
    original_id = initial_txn.id
    logger.debug("Stored original transaction ID: %s", original_id)

    # Update the transaction
    updated_amount = 25.00
    updated_desc = "Test Transaction 2 - Updated"
    logger.debug(
        "Preparing transaction update with properties: %s",
        {
            "id": update_id,
            "new_amount": updated_amount,
            "new_description": updated_desc,
            "expected_amount_cents": int(updated_amount * -100),
        },
    )

    update_txn = pd.DataFrame(
        [
            {
                "_id": update_id,
                "_account": "acc_test123",
                "_connection": "conn_test123",
                "created_at": "2024-01-01T00:00:00Z",
                "date": "2024-01-01T00:00:00Z",
                "description": updated_desc,
                "amount": updated_amount,
                "balance": 1000.00,
                "type": "DEBIT",
            }
        ]
    )
    logger.debug("Created update DataFrame: %s", update_txn.to_dict("records"))

    # Use API to update transaction
    load_transactions_into_actual(update_txn, test_mapping, actual_client)
    logging.info("Transaction update completed")

    # SQL verification of update
    query = (
        select(Transactions)
        .join(Accounts)
        .where(Transactions.financial_id == update_id, Transactions.tombstone == 0)
    )
    logger.debug("Executing post-update verification SQL query: %s", query)
    updated_txn = actual_client.session.exec(query).first()

    if not updated_txn:
        logger.error("Failed to find updated transaction with ID: %s", update_id)
        raise Exception("Test failed: Transaction missing after update")

    logger.debug(
        "Updated transaction state: %s",
        {
            "id": updated_txn.id,
            "amount": updated_txn.amount,
            "amount_dollars": updated_txn.amount / 100,
            "imported_description": updated_txn.imported_description,
            "account_id": updated_txn.acct,
        },
    )

    expected_amount_cents = int(updated_amount * -100)
    if updated_txn.amount != expected_amount_cents:
        logger.error(
            "Updated amount mismatch: expected %d cents, got %d cents",
            expected_amount_cents,
            updated_txn.amount,
        )
        raise Exception(
            f"Test failed: Updated amount incorrect. Expected {updated_amount * -1}, got {updated_txn.amount/100}"
        )
    if updated_txn.imported_description != updated_desc:
        logger.error(
            "Updated description mismatch: expected '%s', got '%s'",
            updated_desc,
            updated_txn.imported_description,
        )
        raise Exception("Test failed: Description was not updated")
    if updated_txn.id != original_id:
        logger.error(
            "Transaction ID changed: expected %s, got %s", original_id, updated_txn.id
        )
        raise Exception("Test failed: Transaction ID changed after update")
    logger.info(
        "Post-update check: Successfully verified transaction update with all properties"
    )

    # Cleanup
    logger.debug("Marking test transaction as deleted (tombstone=1)")
    updated_txn.tombstone = 1
    actual_client.session.add(updated_txn)
    actual_client.session.commit()
    logger.debug("Cleanup completed for transaction ID: %s", update_id)
