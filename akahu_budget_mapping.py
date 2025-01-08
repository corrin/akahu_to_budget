# THis script is responsible for reading from Akahu, Actual Budget and YNAB
# ANd creating a mapping JSON
#
# It's also handy because it acts as a sanity test of the APIs
# If this works then you know that connecting to all three is working, and there's no risk of breaking your budgets.

import os
import pathlib
import logging
from datetime import datetime
from dotenv import load_dotenv
from actual import Actual

# Import from our modules package
from modules import (
    fetch_akahu_accounts,
    fetch_actual_accounts,
    fetch_ynab_accounts
)
from modules.account_mapper import (
    load_existing_mapping,
    merge_and_update_mapping,
    match_accounts,
    save_mapping,
    check_for_changes,
    remove_seq
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)

# Load environment variables from the parent directory's .env file
load_dotenv(dotenv_path=pathlib.Path(__file__).parent / '.env')

required_envs = [
    'ACTUAL_SERVER_URL',
    'ACTUAL_PASSWORD',
    'ACTUAL_ENCRYPTION_KEY',
    'ACTUAL_SYNC_ID',
    'AKAHU_USER_TOKEN',
    'AKAHU_APP_TOKEN',
    'AKAHU_PUBLIC_KEY',
    'OPENAI_API_KEY',
    'YNAB_BEARER_TOKEN',
    'YNAB_BUDGET_ID',
]

SYNC_TO_YNAB = os.environ.get('SYNC_TO_YNAB', 'False') == 'True'
SYNC_TO_AB = os.environ.get('SYNC_TO_AB', 'False') == 'True'

if not SYNC_TO_YNAB and not SYNC_TO_AB:
    logging.error("Environment variable SYNC_TO_AB or SYNC_TO_YNAB must be True.")
    raise EnvironmentError("Environment variable SYNC_TO_AB or SYNC_TO_YNAB must be True.")

# Load environment variables into a dictionary for validation
ENVs = {key: os.getenv(key) for key in required_envs}
DEBUG = os.environ.get('DEBUG', 'False') == 'True'

if DEBUG:
    for key, value in ENVs.items():
        logging.info("Environment variable {key}: {value}".format(key=key, value=value))

# Validate that all environment variables are loaded
for key, value in ENVs.items():
    if value is None:
        logging.error(f"Environment variable {key} is missing.")
        raise EnvironmentError(f"Missing required environment variable: {key}")

def main():
    logging.info("Starting Akahu API integration script.")

    latest_actual_accounts = {}
    latest_ynab_accounts = {}

    if SYNC_TO_AB:
        with Actual(
            base_url=ENVs['ACTUAL_SERVER_URL'],
            password=ENVs['ACTUAL_PASSWORD'],
            file=ENVs['ACTUAL_SYNC_ID'],
            encryption_password=ENVs['ACTUAL_ENCRYPTION_KEY']
        ) as actual:
            logging.info("Actual Budget API initialized successfully.")
            latest_actual_accounts = fetch_actual_accounts(actual)
            logging.info(f"Fetched {len(latest_actual_accounts)} Actual Budget accounts.")
    else:
        logging.info("Not syncing to Actual Budget")

    if SYNC_TO_YNAB:
        latest_ynab_accounts = fetch_ynab_accounts()
        logging.info(f"Fetched {len(latest_ynab_accounts)} YNAB accounts.")
    else:
        logging.info("Not syncing to YNAB")

    # Step 0: Load existing mapping and validate
    existing_akahu_accounts, existing_actual_accounts, existing_ynab_accounts, existing_mapping = load_existing_mapping(generate_stub=True)

    # Retrofit budget IDs to existing mappings to avoid having to manually remap accounts
    # This is a one-time update for existing mappings that don't have these fields
    for mapping in existing_mapping.values():
        if 'ynab_account_id' in mapping and 'ynab_budget_id' not in mapping:
            mapping['ynab_budget_id'] = os.getenv('YNAB_BUDGET_ID')
        if 'actual_account_id' in mapping and 'actual_budget_id' not in mapping:
            mapping['actual_budget_id'] = os.getenv('ACTUAL_SYNC_ID')

    # Step 1: Fetch Akahu accounts
    latest_akahu_accounts = fetch_akahu_accounts()

    # Step 4: Validate and update existing mapping
    existing_mapping, akahu_accounts, actual_accounts, ynab_accounts = merge_and_update_mapping(
        existing_mapping,
        latest_akahu_accounts,
        latest_actual_accounts,
        latest_ynab_accounts,
        existing_akahu_accounts,
        existing_actual_accounts,
        existing_ynab_accounts
    )

    akahu_accounts = dict(sorted(
        akahu_accounts.items(),
        key=lambda x: x[1]['name'].lower()
    ))

    new_mapping = existing_mapping.copy()

    # Compare accounts for changes
    (akahu_accounts_match, actual_accounts_match, ynab_accounts_match) = check_for_changes(
        existing_akahu_accounts, 
        latest_akahu_accounts, 
        existing_actual_accounts, 
        latest_actual_accounts, 
        existing_ynab_accounts, 
        latest_ynab_accounts
    )

    # Only proceed with matching if changes are detected
    if akahu_accounts_match and actual_accounts_match and ynab_accounts_match:
        logging.info("No changes detected in Akahu, Actual, or YNAB accounts. Skipping match")
    else:
        # Step 6: Match Akahu accounts to YNAB accounts interactively
        if SYNC_TO_YNAB:
            new_mapping = match_accounts(new_mapping, akahu_accounts, ynab_accounts, "ynab", use_openai=True)

        # Step 5: Match Akahu accounts to Actual accounts interactively
        if SYNC_TO_AB:
            new_mapping = match_accounts(new_mapping, akahu_accounts, actual_accounts, "actual", use_openai=True)

    # Step 7: Save the final mapping
    data_to_save = {
        "akahu_accounts": akahu_accounts,
        "actual_accounts": actual_accounts,
        "ynab_accounts": ynab_accounts,
        "mapping": new_mapping
    }

    data_without_seq = remove_seq(data_to_save)
    save_mapping(data_without_seq)

if __name__ == "__main__":
    main()