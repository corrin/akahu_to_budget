"""Module for fetching account information from various services."""

import os
import logging
import requests
from actual.queries import get_accounts, get_account
from modules.config import AKAHU_ENDPOINT, AKAHU_HEADERS, YNAB_ENDPOINT, YNAB_HEADERS


def is_simple_value(value):
    """Check if the value is a trivial type: int, float, str, bool, or None"""
    return isinstance(value, (int, float, str, bool)) or value is None


def fetch_akahu_accounts():
    """Fetch accounts from Akahu API"""

    logging.info("Fetching Akahu accounts...")
    response = requests.get(f"{AKAHU_ENDPOINT}/accounts", headers=AKAHU_HEADERS)
    if response.status_code != 200:
        logging.error(
            f"Failed to fetch Akahu accounts: {response.status_code} {response.text}"
        )
        raise RuntimeError(f"Failed to fetch Akahu accounts: {response.status_code}")

    accounts_data = response.json().get("items", [])
    akahu_accounts = {}
    for acc in accounts_data:
        if acc.get("status", "").upper() == "ACTIVE":
            acc_copy = acc.copy()

            # Rename '_id' to 'id'
            acc_copy = {
                "id" if key == "_id" else key: value for key, value in acc_copy.items()
            }

            # Transform 'connection' to keep only the 'name'
            if "connection" in acc_copy and isinstance(acc_copy["connection"], dict):
                acc_copy["connection"] = acc_copy["connection"].get(
                    "name", "Unknown Connection"
                )

            akahu_accounts[acc_copy["id"]] = acc_copy

    logging.info(f"Fetched {len(akahu_accounts)} Akahu accounts.")
    return akahu_accounts


def fetch_actual_accounts(actual_client):
    """Fetch accounts from Actual Budget"""
    try:
        actual_client.download_budget()
        logging.info("Budget downloaded successfully.")

        latest_actual_accounts = get_accounts(actual_client.session)
        open_actual_accounts = {
            acc.id: {
                key: value
                for key, value in acc.__dict__.items()
                if not callable(value)
                and not key.startswith("_")
                and is_simple_value(value)
            }
            for acc in latest_actual_accounts
            if not acc.closed
        }
        logging.info(f"Fetched {len(open_actual_accounts)} open Actual accounts.")
        return open_actual_accounts
    except Exception as e:
        logging.error(f"Failed to fetch Actual accounts: {e}")
        raise


def fetch_ynab_accounts():
    """Fetch accounts from YNAB"""
    ynab_endpoint = "https://api.ynab.com/v1/"
    ynab_headers = {"Authorization": f"Bearer {os.getenv('YNAB_BEARER_TOKEN')}"}

    logging.info("Fetching YNAB accounts...")
    try:
        ynab_budget_id = os.getenv("YNAB_BUDGET_ID")
        if not ynab_budget_id:
            raise ValueError("YNAB_BUDGET_ID environment variable is not set.")

        accounts_json = requests.get(
            f"{ynab_endpoint}budgets/{ynab_budget_id}/accounts", headers=ynab_headers
        ).json()

        ynab_accounts = {}
        for account in accounts_json.get("data", {}).get("accounts", []):
            if not account.get("closed", False):
                ynab_accounts[account["id"]] = {
                    key: value
                    for key, value in account.items()
                    if is_simple_value(value)
                }
        logging.info(
            f"Fetched {len(ynab_accounts)} YNAB accounts for budget {ynab_budget_id}."
        )
        return ynab_accounts
    except Exception as e:
        logging.error(f"Failed to fetch YNAB accounts: {e}")
        raise


def trigger_akahu_refresh():
    """Ask Akahu to refresh all connected accounts from the banks.

    Akahu refreshes from banks on its own schedule (roughly daily). Without
    this nudge a manual sync just replays whatever Akahu already has, which
    in practice is usually several hours stale — and the symptom reported in
    issue #21 was exactly that.

    Best-effort: on failure we warn and continue, so a flaky /refresh
    endpoint never blocks the sync itself. Any real auth/connectivity
    problem will surface loudly on the very next Akahu API call anyway.
    """
    try:
        response = requests.post(
            f"{AKAHU_ENDPOINT}/refresh", headers=AKAHU_HEADERS, timeout=30
        )
        response.raise_for_status()
        logging.info("Triggered Akahu refresh of all connected accounts.")
    except requests.RequestException as e:
        logging.warning(
            f"Akahu refresh request failed (continuing with sync): {e}"
        )


def get_akahu_balance(akahu_account_id, akahu_endpoint, akahu_headers):
    """Fetch the balance for an Akahu account."""
    try:
        response = requests.get(
            f"{akahu_endpoint}/accounts/{akahu_account_id}", headers=akahu_headers
        )
        if response.status_code != 200:
            logging.error(
                f"Failed to fetch balance for account {akahu_account_id}. "
                f"Status code: {response.status_code}, Response: {response.text}"
            )
            return None
        account_data = response.json()
        item = account_data.get("item", {})
        balance = item.get("balance", {}).get("current")
        return balance
    except Exception as e:
        logging.error(f"Error fetching balance for account {akahu_account_id}: {e}")
        raise


def get_actual_balance(actual, actual_account_id):
    """Fetch the balance for an Actual Budget account.
    Returns balance in cents for consistency with other systems."""
    try:
        with actual.session as session:
            account = get_account(session, actual_account_id)
            if account is None:
                logging.error(f"Account '{actual_account_id}' not found.")
                return None

            # Convert from dollars to cents since Actual stores balances in dollars
            total_balance = int(account.balance * 100)
            return total_balance
    except Exception as e:
        logging.error(
            f"Failed to fetch balance for Actual account ID {actual_account_id}: {e}"
        )
        raise


def get_ynab_balance(ynab_budget_id, ynab_account_id):
    uri = f"{YNAB_ENDPOINT}budgets/{ynab_budget_id}/accounts/{ynab_account_id}"
    response = requests.get(uri, headers=YNAB_HEADERS)
    response.raise_for_status()
    account_info = response.json()
    return account_info["data"]["account"]["balance"]
