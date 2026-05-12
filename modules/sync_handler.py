from datetime import datetime
import logging
from modules.account_fetcher import get_akahu_balance, get_ynab_balance
from modules.account_mapper import load_existing_mapping, save_mapping
from modules.transaction_handler import (
    clean_txn_for_ynab,
    create_adjustment_txn_ynab,
    get_all_akahu,
    handle_tracking_account_actual,
    load_transactions_into_actual,
    load_transactions_into_ynab,
)
from modules.config import (
    RUN_SYNC_TO_AB,
    RUN_SYNC_TO_YNAB,
    YNAB_ENDPOINT,
    YNAB_HEADERS,
    AKAHU_ENDPOINT,
    AKAHU_HEADERS,
    FORCE_REFRESH,
    DEBUG_SYNC,
)

try:
    from actual.protobuf_models import SyncRequest
except ImportError:
    SyncRequest = None


def get_account_priority(account_entry):
    """
    Determine processing priority for accounts.
    Returns 0 for On Budget accounts (process first)
    Returns 1 for Tracking accounts (process second)
    """
    account_type = account_entry[1].get("account_type", "On Budget")
    if account_type == "On Budget":
        return 0
    elif account_type == "Tracking":
        return 1
    else:
        logging.warning(f"Unknown account type: {account_type}, treating as Tracking")
        return 1


def update_mapping_timestamps(
    successful_ab_syncs=None,
    successful_ynab_syncs=None,
    mapping_file="akahu_budget_mapping.json",
):
    """Update sync timestamps for multiple accounts in a single operation."""
    akahu_accounts, actual_accounts, ynab_accounts, mappings = load_existing_mapping(
        mapping_file
    )
    current_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    if successful_ab_syncs:
        for akahu_id in successful_ab_syncs:
            if akahu_id in mappings and not mappings[akahu_id].get("actual_do_not_map"):
                mappings[akahu_id]["actual_synced_datetime"] = current_time

    if successful_ynab_syncs:
        for akahu_id in successful_ynab_syncs:
            if akahu_id in mappings and not mappings[akahu_id].get("ynab_do_not_map"):
                mappings[akahu_id]["ynab_synced_datetime"] = current_time

    save_mapping(
        {
            "akahu_accounts": akahu_accounts,
            "actual_accounts": actual_accounts,
            "ynab_accounts": ynab_accounts,
            "mapping": mappings,
        },
        mapping_file,
    )


def sync_to_ynab(mapping_list, debug_mode=None):
    """Sync transactions from Akahu to YNAB.
    
    Args:
        mapping_list: Dictionary of account mappings
        debug_mode: Debug mode setting. 'all' to print all transaction IDs,
                   or a specific Akahu transaction ID for verbose debugging.
    """
    successful_syncs = set()
    transactions_uploaded = 0

    # Sort accounts to process on-budget accounts first
    sorted_accounts = sorted(mapping_list.items(), key=get_account_priority)

    for akahu_account_id, mapping_entry in sorted_accounts:
        ynab_budget_id = mapping_entry.get("ynab_budget_id")
        ynab_account_id = mapping_entry.get("ynab_account_id")
        ynab_account_name = mapping_entry.get("ynab_account_name")
        akahu_account_name = mapping_entry.get("akahu_name")
        account_type = mapping_entry.get("account_type", "On Budget")
        last_reconciled_at = mapping_entry.get(
            "ynab_synced_datetime", "2024-01-01T00:00:00Z"
        )
        if mapping_entry.get("ynab_do_not_map"):
            logging.debug(
                f"Skipping sync to YNAB for Akahu account {akahu_account_id}: account is configured to not be mapped."
            )
            continue

        if not (ynab_budget_id and ynab_account_id):
            logging.warning(
                f"Skipping sync to YNAB for Akahu account {akahu_account_id}: Missing YNAB IDs."
            )
            continue

        logging.info(
            f"Processing Akahu account: {akahu_account_name} ({akahu_account_id}) linked to YNAB account: {ynab_account_name} ({ynab_account_id})"
        )
        logging.info(f"Last synced: {last_reconciled_at}")

        if account_type == "Tracking":
            logging.info(f"Working on tracking account: {ynab_account_name}")
            akahu_balance = get_akahu_balance(
                akahu_account_id, AKAHU_ENDPOINT, AKAHU_HEADERS
            )

            # Update the mapping with the latest balance
            mapping_entry["akahu_balance"] = akahu_balance

            # Get balances (YNAB uses milliunits internally)
            ynab_balance_milliunits = get_ynab_balance(ynab_budget_id, ynab_account_id)
            akahu_balance = get_akahu_balance(
                akahu_account_id, AKAHU_ENDPOINT, AKAHU_HEADERS
            )

            # Convert YNAB milliunits to dollars for comparison
            ynab_balance_dollars = ynab_balance_milliunits / 1000

            # Log balance comparison
            from modules.transaction_handler import log_balance_comparison

            log_balance_comparison("Akahu", akahu_balance, "YNAB", ynab_balance_dollars)

            # Convert Akahu balance to milliunits for YNAB
            akahu_balance_milliunits = int(akahu_balance * 1000)

            if ynab_balance_milliunits != akahu_balance_milliunits:
                create_adjustment_txn_ynab(
                    ynab_budget_id,
                    ynab_account_id,
                    akahu_balance_milliunits,
                    ynab_balance_milliunits,
                    YNAB_ENDPOINT,
                    YNAB_HEADERS,
                )
                logging.info(f"Created balance adjustment for {ynab_account_name}")
                transactions_uploaded += 1
            successful_syncs.add(akahu_account_id)

        elif account_type == "On Budget":
            akahu_df = get_all_akahu(
                akahu_account_id, AKAHU_ENDPOINT, AKAHU_HEADERS, last_reconciled_at
            )

            if akahu_df is not None and not akahu_df.empty:
                # Clean and prepare transactions for YNAB
                cleaned_txn = clean_txn_for_ynab(akahu_df, ynab_account_id)

                # Load transactions into YNAB
                transactions_uploaded += load_transactions_into_ynab(
                    cleaned_txn,
                    mapping_entry["ynab_budget_id"],
                    mapping_entry["ynab_account_id"],
                    YNAB_ENDPOINT,
                    YNAB_HEADERS,
                    debug_mode=debug_mode
                )
                successful_syncs.add(akahu_account_id)
        else:
            logging.error(f"Unknown account type for Akahu account: {akahu_account_id}")

    if successful_syncs:
        update_mapping_timestamps(successful_ynab_syncs=successful_syncs)
    return transactions_uploaded


def sync_to_ab(actual, mapping_list, debug_mode=None):
    """Sync transactions from Akahu to Actual Budget.
    
    Args:
        actual: Actual Budget client instance
        mapping_list: Dictionary of account mappings
        debug_mode: Debug mode setting. 'all' to print all transaction IDs,
                   or a specific Akahu transaction ID for verbose debugging.
    """
    # Force a complete refresh of the budget at the start
    if FORCE_REFRESH:
        logging.info(
            "Force refresh requested - closing session and downloading fresh budget..."
        )
        if hasattr(actual, "_session") and actual._session:
            actual._session.close()
            actual._session = None
        actual.download_budget()  # Force a fresh download
        actual.sync()  # Sync with server
        logging.info("Budget refresh complete")

    successful_ab_syncs = set()
    transactions_uploaded = 0

    # Sort accounts to process on-budget accounts first
    sorted_accounts = sorted(mapping_list.items(), key=get_account_priority)

    for akahu_account_id, mapping_entry in sorted_accounts:
        actual_account_id = mapping_entry.get("actual_account_id")
        actual_account_name = mapping_entry.get("actual_account_name")
        akahu_account_name = mapping_entry.get("akahu_name")
        account_type = mapping_entry.get("account_type", "On Budget")
        last_reconciled_at = mapping_entry.get(
            "actual_synced_datetime", "2024-01-01T00:00:00Z"
        )

        if mapping_entry.get("actual_do_not_map"):
            logging.debug(
                f"Skipping sync to Actual Budget for Akahu account {akahu_account_id}: account is configured to not be mapped."
            )
            continue

        if not (
            mapping_entry.get("actual_budget_id")
            and mapping_entry.get("actual_account_id")
        ):
            logging.warning(
                f"Skipping sync to Actual Budget for Akahu account {akahu_account_id}: Missing Actual Budget IDs."
            )
            continue

        logging.info(
            f"Processing Akahu account: {akahu_account_name} ({akahu_account_id}) linked to Actual account: {actual_account_name} ({actual_account_id})"
        )
        logging.info(f"Last synced: {last_reconciled_at}")

        if account_type == "Tracking":
            # Update balance for mapping entry
            akahu_balance = get_akahu_balance(
                akahu_account_id, AKAHU_ENDPOINT, AKAHU_HEADERS
            )
            if akahu_balance is None:
                logging.error(
                    f"Could not get balance for tracking account {mapping_entry['akahu_name']}"
                )
                continue

            mapping_entry["akahu_balance"] = akahu_balance
            transactions_uploaded += handle_tracking_account_actual(
                mapping_entry, actual
            )  # Note either 1 or 0 returned
            successful_ab_syncs.add(akahu_account_id)
        elif account_type == "On Budget":
            akahu_df = get_all_akahu(
                akahu_account_id, AKAHU_ENDPOINT, AKAHU_HEADERS, last_reconciled_at
            )

            if akahu_df is not None and not akahu_df.empty:
                logging.info("About to load transactions into Actual Budget...")
                transactions_uploaded += load_transactions_into_actual(
                    akahu_df, mapping_entry, actual, debug_mode=debug_mode
                )
                successful_ab_syncs.add(akahu_account_id)
        else:
            logging.error(f"Unknown account type for Akahu account: {akahu_account_id}")
            raise

    # Commit all changes after processing all accounts
    any_transactions_processed = transactions_uploaded > 0
    if any_transactions_processed:
        if DEBUG_SYNC:
            logging.info("Finished processing all accounts, about to commit...")
        try:
            commit_result = actual.commit()
            if DEBUG_SYNC:
                logging.info(f"Commit result: {commit_result}")
            actual.download_budget()
        except Exception as e:
            logging.error(f"Error during commit: {str(e)}")
            logging.error(f"Error type: {type(e)}")
            raise

    if successful_ab_syncs:
        update_mapping_timestamps(successful_ab_syncs=successful_ab_syncs)
    return transactions_uploaded
