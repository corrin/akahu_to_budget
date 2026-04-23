"""Module for handling transaction processing and syncing."""

from datetime import datetime, timedelta
import decimal
import json
import logging
import pandas as pd
import requests
from actual.queries import (
    create_transaction,
    get_ruleset,
    reconcile_transaction,
    get_categories,
    get_payee,
    get_payees,
    get_account,
    match_transaction,
    set_transaction_payee,
)
from typing import Dict

from modules.account_fetcher import get_actual_balance
from modules.config import AKAHU_HEADERS


def get_cached_names(actual) -> tuple[Dict[str, str], Dict[str, str]]:
    """Get cached category and payee names.

    Args:
        actual: Actual instance with active session

    Returns:
        Tuple of (category_names, payee_names) dictionaries mapping IDs to names

    Raises:
        RuntimeError: If there is any error accessing the Actual Budget database
    """
    try:
        # Get all categories using the API
        categories = get_categories(actual.session)
        category_names = {cat.id: cat.name for cat in categories} if categories else {}
        category_names[None] = "Uncategorized"
        if not categories:
            logging.info(
                "No categories found in Actual Budget - this is normal for a new budget"
            )

        # Get all payees using the API
        payees = get_payees(actual.session)
        payee_names = {payee.id: payee.name for payee in payees} if payees else {}
        if not payees:
            logging.info(
                "No payees found in Actual Budget - this is normal for a new budget"
            )

        return category_names, payee_names

    except Exception as e:
        logging.error(
            f"Error accessing Actual Budget database - aborting transaction processing: {str(e)}"
        )
        raise RuntimeError(
            f"Failed to access Actual Budget database: {str(e)}"
        ) from None


def log_balance_comparison(
    source_name: str,
    source_balance: float,
    dest_name: str,
    dest_balance: float,
    dest_in_cents: bool = False,
):
    """Log a comparison between source and destination balances.

    Formats and logs balances from two systems in a consistent format for comparison.

    Args:
        source_name: Name of the source system (e.g., 'Akahu')
        source_balance: Balance from source system (in dollars)
        dest_name: Name of the destination system (e.g., 'Actual', 'YNAB')
        dest_balance: Balance from destination system (in cents if dest_in_cents=True)
        dest_in_cents: Whether destination balance is in cents (default: False)
    """
    # Convert source balance to cents for comparison
    source_cents = int(decimal.Decimal(str(source_balance)) * 100)

    # Use destination balance as-is if it's already in cents
    dest_cents = (
        int(dest_balance)
        if dest_in_cents
        else int(decimal.Decimal(str(dest_balance)) * 100)
    )

    # Convert both to dollars for display
    source_dollars = decimal.Decimal(source_cents) / 100
    dest_dollars = decimal.Decimal(dest_cents) / 100

    logging.info(
        f"Balances (in dollars) - "
        f"{source_name}: ${source_dollars:,.2f} | "
        f"{dest_name}: ${dest_dollars:,.2f}"
    )


def get_all_akahu(
    akahu_account_id, akahu_endpoint, akahu_headers, last_reconciled_at=None
):
    """Fetch all transactions from Akahu for a given account, supporting pagination."""
    query_params = {}
    res = None
    total_txn = 0

    if last_reconciled_at:
        last_reconciled_at_dt = datetime.fromisoformat(
            last_reconciled_at.replace("Z", "+00:00")
        )
        start_time = last_reconciled_at_dt - timedelta(weeks=1)
        query_params["start"] = start_time.isoformat().replace("+00:00", "Z")
    else:
        query_params["start"] = "2024-01-01T00:00:00Z"

    next_cursor = "first time"
    while next_cursor is not None:
        if next_cursor != "first time":
            query_params["cursor"] = next_cursor

        try:
            response = requests.get(
                f"{akahu_endpoint}/accounts/{akahu_account_id}/transactions",
                params=query_params,
                headers=akahu_headers,
            )
            response.raise_for_status()
            
            akahu_txn_json = response.json()
            akahu_txn = pd.DataFrame(akahu_txn_json.get("items", []))

            # TODO: Enrichment endpoint requires different authentication
            # for _, txn in akahu_txn.iterrows():
            #     enrich_transaction(txn, akahu_endpoint, akahu_headers)

        except requests.exceptions.RequestException as e:
            logging.error(
                f"Failed to fetch transactions from Akahu "
                f"for account {akahu_account_id}: {str(e)}"
            )
            raise RuntimeError(
                f"Failed to fetch Akahu transactions: {str(e)}"
            ) from None

        if res is None:
            res = akahu_txn.copy()
        else:
            res = pd.concat([res, akahu_txn])

        num_txn = akahu_txn.shape[0]
        total_txn += num_txn
        # Check if we've reached the end of pagination:
        # - No transactions in this page, or
        # - No cursor in response, or
        # - No next page token in cursor
        has_more_pages = (
            num_txn > 0
            and "cursor" in akahu_txn_json
            and "next" in akahu_txn_json["cursor"]
        )

        if has_more_pages:
            next_cursor = akahu_txn_json["cursor"]["next"]
        else:
            next_cursor = None  # End of pagination

    if total_txn > 0:
        logging.info(f"Fetched {total_txn} transactions from Akahu.")
    return res


def load_transactions_into_actual(transactions, mapping_entry, actual, debug_mode=None):
    """Load transactions into Actual Budget using the mapping information.
    
    Args:
        transactions: DataFrame of transactions to load
        mapping_entry: Dictionary containing mapping information
        actual: Actual Budget client instance
        debug_mode: Debug mode setting. 'all' to print all transaction IDs,
                   or a specific Akahu transaction ID for verbose debugging.
    """
    if transactions is None or transactions.empty:
        logging.info("No transactions to load into Actual.")
        return

    actual_account_id = mapping_entry["actual_account_id"]
    imported_transactions = []

    # Get cached names for rule changes - this will raise RuntimeError if it fails
    category_names, payee_names = get_cached_names(actual)

    # Get ruleset - no rules is a valid state for new budgets
    try:
        ruleset = get_ruleset(actual.session)
        if ruleset is None:
            logging.info(
                "No ruleset found in Actual Budget - this is normal for a new budget"
            )
    except Exception as e:
        logging.error(
            "Database error while getting ruleset - "
            f"aborting transaction processing: {str(e)}"
        )
        raise RuntimeError(
            f"Failed to access Actual Budget database: {str(e)}"
        ) from None

    for _, txn in transactions.iterrows():
        transaction_date = txn.get("date")
        payee_name = txn.get("description")
        notes = txn.get("description")
        imported_id = txn.get("_id")
        raw_amount = txn.get("amount")
        try:
            amount = decimal.Decimal(raw_amount).quantize(decimal.Decimal("0.0001"))
        except (decimal.InvalidOperation, TypeError, ValueError) as e:
            raise RuntimeError(
                f"Could not parse amount={raw_amount!r} for Akahu transaction "
                f"{imported_id} (date={transaction_date}, payee={payee_name!r})"
            ) from e
        cleared = True

        try:
            # Convert UTC to NZ date
            nzt_date_str = convert_to_nzt(transaction_date)
            parsed_date = datetime.strptime(nzt_date_str, "%Y-%m-%d").date()

            # Debug logging for transaction ID
            if debug_mode == 'all' or debug_mode == imported_id:
                txn_details = f"{imported_id} - {payee_name} ${amount}"
                logging.info(f"Processing transaction: {txn_details}")

            if debug_mode == imported_id:
                logging.info(f"\nDEBUG: Verbose logging for transaction {imported_id}")
                logging.info(f"Date: {parsed_date}")
                logging.info(f"Payee: {payee_name}")
                logging.info(f"Amount: ${amount}")
                logging.info(f"Notes: {notes}")
                logging.info("Attempting to reconcile transaction...")

            # If we're debugging this specific transaction, use lower level functions
            # to provide more detailed debugging information about what's happening
            if debug_mode == imported_id:
                account = get_account(actual.session, actual_account_id)
                match = match_transaction(
                    actual.session,
                    parsed_date,
                    account,
                    payee_name,
                    amount,
                    imported_id,
                    imported_transactions
                )

                logging.info("DEBUG: Looking for matching transaction...")
                if match:
                    logging.info("DEBUG: Found matching transaction:")
                    logging.info(f"  * id: {match.id}")
                    logging.info(f"  * date: {match.date}")
                    logging.info(f"  * payee_id: {match.payee_id}")
                    logging.info(f"  * payee: {payee_names.get(match.payee_id, 'Unknown')}")
                    logging.info(f"  * amount: ${match.amount}")
                    logging.info(f"  * notes: {match.notes}")
                    logging.info("Attempting to update fields...")

                    # Store original state for debugging
                    orig_state = {
                        'notes': match.notes,
                        'date': match.date,
                        'payee_id': match.payee_id
                    }
                    
                    # Don't update fields for matches - we just want to detect duplicates
                    logging.info("DEBUG: Found duplicate - not updating fields")
                    reconciled_transaction = match
                else:
                    logging.info("DEBUG: No matching transaction found, will create new")
                    reconciled_transaction = create_transaction(
                        actual.session,
                        parsed_date,
                        account,
                        payee_name,
                        notes,
                        None,  # category
                        amount,
                        imported_id,
                        cleared,
                        payee_name  # imported_payee
                    )
            else:
                # Normal non-debug path using reconcile_transaction
                # Normal non-debug path using reconcile_transaction
                # Set update_existing=False since we want to detect duplicates but not update them
                reconciled_transaction = reconcile_transaction(
                    actual.session,
                    date=parsed_date,
                    account=actual_account_id,
                    payee=payee_name,
                    notes=notes,
                    amount=amount,
                    imported_id=imported_id,
                    cleared=cleared,
                    imported_payee=payee_name,
                    already_matched=imported_transactions,
                    update_existing=False  # Don't update fields for matches
                )

            if not reconciled_transaction.changed():
                txn_details = f"{imported_id} - {payee_name} ${amount}"
                if debug_mode == imported_id:
                    logging.info("DEBUG: Transaction was not modified")
                elif debug_mode == 'all':
                    logging.info(f"Skipped duplicate transaction: {txn_details}")
                else:
                    logging.debug(f"Transaction already exists, skipping rule application and import.")
                continue  # Skip to the next transaction

            if ruleset is not None:
                # Store transaction state before running rules
                pre_rules_state = vars(reconciled_transaction).copy()
                pre_rules_payee_id = reconciled_transaction.payee_id
                ruleset.run(reconciled_transaction)

                # ruleset.run assigns payee_id directly, which bypasses
                # set_transaction_payee's side effect of materialising the
                # mirror transaction when the new payee points at another
                # account. Re-apply via set_transaction_payee so transfers
                # are created at import time rather than only after the
                # user edits the transaction in the Actual UI.
                new_payee_id = reconciled_transaction.payee_id
                if new_payee_id and new_payee_id != pre_rules_payee_id:
                    new_payee = get_payee(actual.session, new_payee_id)
                    if new_payee is not None and new_payee.transfer_acct:
                        # Ensure .account is materialised; set_transaction_payee
                        # dereferences transaction.account.payee.id internally
                        # and that relationship is lazy-loaded.
                        actual.session.flush()
                        reconciled_transaction.payee_id = pre_rules_payee_id
                        set_transaction_payee(
                            actual.session, reconciled_transaction, new_payee
                        )

                # Compare states to see if rules modified the transaction
                post_rules_state = vars(reconciled_transaction)
                changes = []
                for key, value in post_rules_state.items():
                    if key in pre_rules_state and pre_rules_state[key] != value:
                        # Format category changes
                        if key == "category_id":
                            old_name = category_names.get(
                                pre_rules_state[key], "Unknown"
                            )
                            new_name = category_names.get(value, "Unknown")
                            changes.append(f"category: {old_name} -> {new_name}")
                        # Format payee changes
                        elif key == "payee_id":
                            old_name = payee_names.get(pre_rules_state[key], "Unknown")
                            new_name = payee_names.get(value, "Unknown")
                            changes.append(f"payee: {old_name} -> {new_name}")
                        # Skip internal fields
                        elif not key.startswith("_"):
                            changes.append(f"{key}: {pre_rules_state[key]} -> {value}")
                if changes:
                    logging.info(f"Rules modified transaction {imported_id}:")
                    for change in changes:
                        logging.info(f"  {change}")
                else:
                    logging.debug(f"Rules did not modify transaction {imported_id}")

        except Exception as e:
            logging.error(
                f"Failed to reconcile transaction {imported_id} "
                f"into Actual for account {actual_account_id}: {str(e)}"
            )
            raise RuntimeError(
                f"Failed to process transaction into Actual: {str(e)}"
            ) from None

        if reconciled_transaction.changed():
            imported_transactions.append(reconciled_transaction)
            txn_details = f"on {parsed_date} at {payee_name} for ${amount}"
            logging.info(f"Imported new transaction {txn_details}")
            if notes != payee_name:
                logging.debug(f"Transaction notes: {notes}")
        else:
            txn_details = f"on {parsed_date} at {payee_name} for ${amount}"
            logging.info(f"Skipped existing transaction {txn_details}")

    mapping_entry["actual_synced_datetime"] = datetime.utcnow().strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    # Commit all changes to Actual
    try:
        actual.commit()
        logging.info("Successfully committed changes to Actual Budget")
    except Exception as e:
        logging.error(f"Failed to commit changes to Actual Budget: {str(e)}")
        raise RuntimeError(f"Failed to commit changes to Actual: {str(e)}") from None

    return len(imported_transactions)


def handle_tracking_account_actual(mapping_entry, actual):
    """Handle tracking accounts by checking and adjusting balances."""
    actual_account_id = mapping_entry["actual_account_id"]
    akahu_account_name = mapping_entry["akahu_name"]

    try:
        # Get balances
        akahu_balance_dollars = mapping_entry["akahu_balance"]
        actual_balance_cents = get_actual_balance(actual, actual_account_id)

        # Log balance comparison
        log_balance_comparison(
            "Akahu",
            akahu_balance_dollars,
            "Actual",
            actual_balance_cents,
            dest_in_cents=True,
        )

        # Convert Akahu balance to cents for comparison
        akahu_balance_cents = int(decimal.Decimal(str(akahu_balance_dollars)) * 100)

        if akahu_balance_cents != actual_balance_cents:
            # Calculate adjustment in dollars since Actual expects dollars
            adjustment_dollars = (akahu_balance_cents - actual_balance_cents) / 100

            transaction_date = datetime.utcnow().date()
            payee_name = "Balance Adjustment"
            old_balance = f"${decimal.Decimal(actual_balance_cents)/100:,.2f}"
            new_balance = f"${decimal.Decimal(akahu_balance_cents)/100:,.2f}"
            notes = (
                f"Adjusted from {old_balance} to {new_balance} "
                "to reconcile tracking account"
            )

            # Use the imported create_transaction function with the session directly
            # Note: Actual expects amounts in cents
            create_transaction(
                actual.session,
                date=transaction_date,
                account=actual_account_id,
                payee=payee_name,
                notes=notes,
                amount=adjustment_dollars,  # Actual expects dollars
                imported_id=f"adjustment_{datetime.utcnow().isoformat()}",
                cleared=True,
                imported_payee=payee_name,
            )

            # Commit the adjustment transaction
            try:
                actual.commit()
                amount_str = f"${adjustment_dollars:,.2f}"
                logging.info(
                    f"Created and committed balance adjustment transaction of {amount_str}"
                )
                return 1
            except Exception as e:
                logging.error(
                    f"Failed to commit balance adjustment to Actual Budget: {str(e)}"
                )
                raise RuntimeError(
                    f"Failed to commit balance adjustment to Actual: {str(e)}"
                ) from None
        return 0

    except Exception as e:
        logging.error(f"Error handling tracking account {akahu_account_name}: {str(e)}")
        raise


def enrich_transaction(transaction_data, akahu_endpoint, akahu_headers):
    """Print enrichment data for a transaction."""
    try:
        # Call the Akahu enrichment API with standard auth headers
        response = requests.post(
            "https://api.genie.akahu.io/v1/search",
            headers=AKAHU_HEADERS,  # Use the standard Akahu headers
            json={
                "amount": float(transaction_data.get("amount")),
                "date": transaction_data.get("date"),
                "description": transaction_data.get("description")
            }
        )
        response.raise_for_status()
        logging.info(f"\nTransaction: {json.dumps(transaction_data, indent=2)}")
        logging.info(f"\nEnriched data: {json.dumps(response.json(), indent=2)}")
    except Exception as e:
        logging.error(f"Error enriching transaction: {e}")

def get_payee_name(row):
    """Extract the payee name from the given row.

    Prioritizes the merchant name if available, otherwise uses the description.
    """
    try:
        res = None
        if "merchant" in row and not pd.isna(row["merchant"]):
            if "name" in row["merchant"]:
                res = row["merchant"]["name"]
        if res is None:
            res = row["description"]
    except (TypeError, ValueError) as e:
        logging.error(f"Error extracting payee name from row: {e}, row: {row}")
        res = "Unknown"
    return res


def convert_to_nzt(date_str):
    """Convert a given date string to New Zealand Time (NZT)."""
    try:
        if date_str is None:
            logging.warning("Input date string is None.")
            return None
        # Remove any milliseconds if present before parsing
        if "." in date_str:
            date_str = date_str[:date_str.index(".")] + "Z"
        utc_time = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
        nzt_time = utc_time + timedelta(hours=13)
        return nzt_time.strftime("%Y-%m-%d")
    except ValueError as e:
        logging.error(f"Error converting date string to NZT: {e}, date_str: {date_str}")
        return None


def clean_txn_for_ynab(akahu_txn, ynab_account_id):
    """Clean and transform Akahu transactions to prepare them for YNAB import."""
    akahu_txn["payee_name"] = akahu_txn.apply(get_payee_name, axis=1)
    akahu_txn["memo"] = akahu_txn["description"]
    akahu_txn_useful = akahu_txn[
        ["_id", "date", "amount", "memo", "payee_name"]
    ].rename(columns={"_id": "id"}, errors="ignore")
    akahu_txn_useful["amount"] = akahu_txn_useful["amount"].apply(
        lambda x: str(int(x * 1000))
    )
    akahu_txn_useful["cleared"] = "cleared"
    akahu_txn_useful["date"] = akahu_txn_useful.apply(
        lambda row: convert_to_nzt(row["date"]), axis=1
    )
    akahu_txn_useful["import_id"] = akahu_txn_useful["id"]
    akahu_txn_useful["flag_color"] = "red"
    akahu_txn_useful["account_id"] = ynab_account_id

    return akahu_txn_useful


def get_ynab_transactions(ynab_budget_id, ynab_endpoint, ynab_headers):
    """Fetch all transactions from YNAB for a given budget."""
    uri = f"{ynab_endpoint}budgets/{ynab_budget_id}/transactions"
    try:
        response = requests.get(uri, headers=ynab_headers)
        response.raise_for_status()
        return response.json().get("data", {}).get("transactions", [])
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching transactions from YNAB: {e}")
        if response is not None:
            logging.error(f"API response content: {response.text}")
        raise


def load_transactions_into_ynab(
    akahu_txn, ynab_budget_id, ynab_account_id, ynab_endpoint, ynab_headers,
    debug_mode=None
):
    """Save transactions from Akahu to YNAB.
    
    Args:
        akahu_txn: DataFrame of transactions to load
        ynab_budget_id: YNAB budget ID
        ynab_account_id: YNAB account ID
        ynab_endpoint: YNAB API endpoint
        ynab_headers: YNAB API headers
        debug_mode: Debug mode setting. 'all' to print all transaction IDs,
                   or a specific Akahu transaction ID for verbose debugging.
    """
    uri = f"{ynab_endpoint}budgets/{ynab_budget_id}/transactions"
    transactions_list = akahu_txn.to_dict(orient="records")

    # Debug logging for transactions
    if debug_mode == 'all':
        for txn in transactions_list:
            logging.info(f"Processing transaction: {txn['import_id']} - {txn['payee_name']} ${float(txn['amount'])/1000:.2f}")

    ynab_api_payload = {"transactions": transactions_list}

    try:
        response = requests.post(uri, headers=ynab_headers, json=ynab_api_payload)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(
            f"Failed to post transactions to YNAB "
            f"for account {ynab_account_id}: {str(e)}"
        )
        raise RuntimeError(f"Failed to load transactions into YNAB: {str(e)}") from None

    ynab_response = response.json()
    duplicates = ynab_response["data"].get("duplicate_import_ids", [])
    if duplicates:
        dup_count = len(duplicates)
        dup_str = f"Skipped {dup_count} duplicates"
        
        if debug_mode == 'all':
            logging.info(dup_str)
            # Find the original transactions to show their details
            for dup_id in duplicates:
                matching_txn = next((t for t in transactions_list if t['import_id'] == dup_id), None)
                if matching_txn:
                    logging.info(f"Skipped duplicate: {dup_id} - {matching_txn['payee_name']} ${float(matching_txn['amount'])/1000:.2f}")
    else:
        dup_str = "No duplicates"

    new_txns = ynab_response["data"]["transactions"]
    if not new_txns:
        logging.info(f"No new transactions loaded to YNAB - {dup_str}")
    else:
        num_txns = len(new_txns)
        logging.info(f"Successfully loaded {num_txns} transactions to YNAB - {dup_str}")
        if debug_mode == 'all':
            for txn in new_txns:
                logging.info(f"Imported: {txn['import_id']} - {txn['payee_name']} ${float(txn['amount'])/1000:.2f}")

    return len(ynab_response["data"]["transactions"])


def create_adjustment_txn_ynab(
    ynab_budget_id,
    ynab_account_id,
    akahu_balance,
    ynab_balance,
    ynab_endpoint,
    ynab_headers,
):
    """Create an adjustment transaction in YNAB to reconcile the balance."""
    try:
        balance_difference = akahu_balance - ynab_balance
        if balance_difference == 0:
            logging.info("No adjustment needed; balances are already in sync.")
            return

        uri = f"{ynab_endpoint}budgets/{ynab_budget_id}/transactions"
        transaction = {
            "transaction": {
                "account_id": ynab_account_id,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "amount": int(balance_difference),  # Already in milliunits for YNAB
                "payee_name": "Balance Adjustment",
                "memo": (
                    f"Adjusted from ${ynab_balance/1000:.2f} "
                    f"to ${akahu_balance/1000:.2f} based on retrieved balance"
                ),
                "flag_color": "red",
                "cleared": "cleared",
                "approved": True,
            }
        }

        response = requests.post(uri, headers=ynab_headers, json=transaction)
        response.raise_for_status()
        logging.info(
            f"Created YNAB balance adjustment transaction of ${balance_difference/1000:,.2f}"
        )

    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to create balance adjustment transaction: {e}")
        raise
