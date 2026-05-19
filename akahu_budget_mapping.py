# This script is responsible for reading from Akahu, Actual Budget and YNAB
# And creating a mapping JSON

import os
import pathlib
import logging
from datetime import datetime
from dotenv import load_dotenv

from actual import Actual

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)

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
from modules.config import RUN_SYNC_TO_YNAB, RUN_SYNC_TO_AB, RUN_SYNC_TO_SURE



# Load environment variables from the parent directory's .env file
load_dotenv(dotenv_path=pathlib.Path(__file__).parent / '.env')

DEBUG = False

# Define required environment variables based on sync settings
logging.info(f"Sync targets - YNAB: {RUN_SYNC_TO_YNAB}, AB: {RUN_SYNC_TO_AB}, SURE: {RUN_SYNC_TO_SURE}")

required_envs = [
    'AKAHU_USER_TOKEN',
    'AKAHU_APP_TOKEN',
]

if RUN_SYNC_TO_AB:
    required_envs.extend([
        'ACTUAL_SERVER_URL',
        'ACTUAL_PASSWORD',
        'ACTUAL_ENCRYPTION_KEY',
        'ACTUAL_SYNC_ID',
    ])

if RUN_SYNC_TO_YNAB:
    required_envs.extend([
        'YNAB_BEARER_TOKEN',
        'YNAB_BUDGET_ID',
    ])

if RUN_SYNC_TO_SURE:
    required_envs.extend([
        'SURE_API_TOKEN',
    ])

# Load environment variables into a dictionary for validation
ENVs = {key: os.getenv(key) for key in required_envs}

if DEBUG:
    for key, value in ENVs.items():
        logging.info("Environment variable {key}: {value}".format(key=key, value=value))

# Validate that all required environment variables are loaded
for key, value in ENVs.items():
    if value is None:
        logging.error(f"Environment variable {key} is missing.")
        raise EnvironmentError(f"Missing required environment variable: {key}")

def main():
    logging.info("Starting Akahu API integration script.")

    latest_actual_accounts = {}
    latest_ynab_accounts = {}

    if RUN_SYNC_TO_AB:
        try:
            with Actual(
                    base_url=ENVs['ACTUAL_SERVER_URL'],
                    password=ENVs['ACTUAL_PASSWORD'],
                    file=ENVs['ACTUAL_SYNC_ID'],
                    encryption_password=ENVs['ACTUAL_ENCRYPTION_KEY']
            ) as actual:
                logging.info("Actual Budget API initialized successfully.")
                latest_actual_accounts = fetch_actual_accounts(actual)
                logging.info(
                    f"Fetched {len(latest_actual_accounts)} Actual Budget accounts.")
        except Exception as e:
            logging.error(f"Failed to initialize Actual Budget API: {e}")
            raise
    else:
        logging.info("Not syncing to Actual Budget")

    latest_ynab_accounts = {}
    if RUN_SYNC_TO_YNAB:
        latest_ynab_accounts = fetch_ynab_accounts()
        logging.info(f"Fetched {len(latest_ynab_accounts)} YNAB accounts.")
    else:
        logging.info("Not syncing to YNAB - skipping YNAB account fetch")

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
        use_openai = bool(os.getenv("OPENAI_API_KEY"))
        logging.info(
            f"Match suggestions: {'OpenAI' if use_openai else 'fuzzy match'} "
            f"(set OPENAI_API_KEY to enable OpenAI)"
        )

        # Step 6: Match Akahu accounts to YNAB accounts interactively
        if RUN_SYNC_TO_YNAB:
            new_mapping = match_accounts(new_mapping, akahu_accounts, ynab_accounts, "ynab", use_openai=use_openai)

        # Step 5: Match Akahu accounts to Actual accounts interactively
        if RUN_SYNC_TO_AB:
            new_mapping = match_accounts(new_mapping, akahu_accounts, actual_accounts, "actual", use_openai=use_openai)
            
    # Interactive Mapping Loop for Sure Finance
    if RUN_SYNC_TO_SURE:
        print("\n" + "="*50)
        print("Sure Finance Interactive Mapping")
        print("="*50)
        print("Please provide the Sure Finance Account ID for your Akahu accounts.")
        print("You can find this ID in the URL when viewing an account in the Sure UI.")
        print("(Press Enter to skip an account if you don't want to map it to Sure)")
        
        for akahu_id, akahu_acc in akahu_accounts.items():
            current_sure_id = new_mapping.get(akahu_id, {}).get("sure_id")
            
            # Skip if already mapped
            if current_sure_id:
                logging.info(f"[{akahu_acc['name']}] is already mapped to Sure ID: {current_sure_id}")
                continue
            
            print(f"\nBank Account: {akahu_acc['name']}")
            user_input = input("Enter Sure Account ID: ").strip()
            
            if user_input:
                if akahu_id not in new_mapping:
                    new_mapping[akahu_id] = {}
                new_mapping[akahu_id]["sure_id"] = user_input
                print(f"Mapped '{akahu_acc['name']}' to Sure ID '{user_input}'.")
            else:
                print("Skipped Akahu mapping to Sure ID.")

    # Step 7: Save the final mapping
    data_to_save = {
        "akahu_accounts": akahu_accounts,
        "actual_accounts": actual_accounts,
        "ynab_accounts": ynab_accounts,
        "mapping": new_mapping
    }

    data_without_seq = remove_seq(data_to_save)
    save_mapping(data_without_seq)
    logging.info("Mapping saved successfully.")

if __name__ == "__main__":
    main()
