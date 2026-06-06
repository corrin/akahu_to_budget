"""Shared non-Flask sync runner for CLI and web entrypoints."""

from contextlib import contextmanager
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv
import httpx
import requests
from actual import Actual

# Populate os.environ before modules.config reads environment variables.
load_dotenv()

from modules.account_fetcher import trigger_akahu_refresh
from modules.mapping_store import load_existing_mapping, save_mapping
from modules.config import RUN_SYNC_TO_AB, RUN_SYNC_TO_YNAB, RUN_SYNC_TO_SURE, AKAHU_ENDPOINT, AKAHU_HEADERS
from modules.config import ENVs
from modules.sync_handler import sync_to_ab, sync_to_ynab
from modules.transaction_handler import get_all_akahu
import sure_client


def configure_logging():
    """Configure logging once for command-line and Flask entrypoints."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("app.log"),
            logging.StreamHandler(),
        ],
    )


@contextmanager
def get_actual_client():
    """Yield an Actual client if Actual sync is enabled; otherwise yield None."""
    if RUN_SYNC_TO_AB:
        try:
            logging.info(
                f"Attempting to connect to Actual server at {ENVs['ACTUAL_SERVER_URL']}"
            )

            with Actual(
                base_url=ENVs["ACTUAL_SERVER_URL"],
                password=ENVs["ACTUAL_PASSWORD"],
                file=ENVs["ACTUAL_SYNC_ID"],
                encryption_password=ENVs["ACTUAL_ENCRYPTION_KEY"],
            ) as client:
                logging.info(f"Connected to AB: {client}")
                yield client
        except (httpx.HTTPError, requests.exceptions.RequestException) as e:
            logging.error(f"Failed to connect to Actual server: {str(e)}")
            if hasattr(e, "response") and e.response is not None:
                logging.error(f"Response status: {e.response.status_code}")
                logging.error(f"Response headers: {dict(e.response.headers)}")
                logging.error(f"Response content: {e.response.text[:500]}")
            raise RuntimeError(
                f"Failed to connect to Actual server: {str(e)}"
            ) from None
    else:
        yield None


def sync_to_sure(mapping_list):
    """Pull transactions from Akahu and push them to Sure Finance."""
    sure_count = 0
    current_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    successful_syncs = set()

    for akahu_id, mapping_entry in mapping_list.items():
        sure_id = mapping_entry.get("sure_id")

        if not sure_id or mapping_entry.get("sure_do_not_map"):
            continue

        akahu_name = mapping_entry.get('name', akahu_id)
        logging.info(f"Syncing Akahu account '{akahu_name}' to Sure Finance...")

        last_reconciled = mapping_entry.get("sure_synced_datetime", "2024-01-01T00:00:00Z")
        akahu_df = get_all_akahu(akahu_id, AKAHU_ENDPOINT, AKAHU_HEADERS, last_reconciled)

        account_failed = False
        if akahu_df is not None and not akahu_df.empty:
            transactions = [row.to_dict() for _, row in akahu_df.iterrows()]
            
            try:
                sure_client.push_transactions(transactions, sure_id)
                sure_count += len(transactions)
            except Exception as e:
                logging.error(f"Error pushing batch to Sure for '{akahu_name}': {e}")
                account_failed = True

        if account_failed:
            logging.warning(
                f"Not advancing sync watermark for '{akahu_name}' due to errors. "
                "Failed transactions will be retried on the next sync."
            )
        else:
            successful_syncs.add(akahu_id)

    # Safely persist watermarks utilizing Corrin's mapping store architecture
    if successful_syncs:
        akahu_accs, actual_accs, ynab_accs, full_mapping = load_existing_mapping()
        for acc_id in successful_syncs:
            if acc_id in full_mapping:
                full_mapping[acc_id]["sure_synced_datetime"] = current_time
                
        save_mapping({
            "akahu_accounts": akahu_accs,
            "actual_accounts": actual_accs,
            "ynab_accounts": ynab_accs,
            "mapping": full_mapping
        })

    return sure_count


def run_sync(account_ids=None, debug_mode=None):
    """Run Akahu sync to enabled budget targets."""
    logging.info("Starting direct sync...")
    actual_count = ynab_count = sure_count = 0

    trigger_akahu_refresh()

    _, _, _, mapping_list = load_existing_mapping()

    if account_ids:
        filtered_mapping = {k: v for k, v in mapping_list.items() if k in account_ids}
        if not filtered_mapping:
            logging.warning(f"No matching accounts found for IDs: {account_ids}")
            return
        logging.info(f"Syncing specific accounts: {', '.join(account_ids)}")
        mapping_list = filtered_mapping

    with get_actual_client() as actual_client:
        if RUN_SYNC_TO_AB and actual_client:
            actual_client.download_budget()
            actual_count = sync_to_ab(actual_client, mapping_list, debug_mode=debug_mode)
            logging.info(f"Synced {actual_count} accounts to Actual Budget.")

        if RUN_SYNC_TO_YNAB:
            ynab_count = sync_to_ynab(mapping_list, debug_mode=debug_mode)
            logging.info(f"Synced {ynab_count} accounts to YNAB.")

    if RUN_SYNC_TO_SURE:
        sure_count = sync_to_sure(mapping_list)
        logging.info(f"Synced {sure_count} transactions to Sure Finance.")

    logging.info(
        f"Sync completed. Actual: {actual_count}, YNAB: {ynab_count}, Sure: {sure_count}"
    )
