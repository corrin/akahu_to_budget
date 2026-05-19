"""Push Akahu transactions to a self-hosted Sure Finance instance."""

import logging
import os
import zoneinfo
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

# Pull configuration at call time, not import time, so test harnesses and the
# config validation in modules.config can reliably see the values.
SURE_DEFAULT_URL = "http://127.0.0.1:8084/api/v1/transactions"
SURE_REQUEST_TIMEOUT_SECONDS = 15
NZ_TIMEZONE = zoneinfo.ZoneInfo("Pacific/Auckland")


def _akahu_to_sure_date(raw_date):
    """Convert an Akahu UTC ISO timestamp to a Sure-friendly NZ-local YYYY-MM-DD.

    Sure anchors transactions to local-time, so a late-evening NZ transaction
    expressed in UTC would otherwise roll back a calendar day.
    """
    if not raw_date:
        return ""
    cleaned = raw_date.split(".", 1)[0]
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1]
    utc_time = datetime.fromisoformat(cleaned).replace(tzinfo=timezone.utc)
    return utc_time.astimezone(NZ_TIMEZONE).strftime("%Y-%m-%d")


def push_to_sure(transaction, sure_account_id):
    """Post a single Akahu transaction dict to Sure Finance."""
    sure_api_token = os.environ.get("SURE_API_TOKEN")
    if not sure_api_token:
        raise RuntimeError(
            "SURE_API_TOKEN is missing — modules.config should have caught this. "
            "Is RUN_SYNC_TO_SURE set correctly?"
        )

    sure_url = os.environ.get("SURE_API_URL", SURE_DEFAULT_URL)

    # Akahu and Sure use opposite sign conventions for depository accounts:
    # Akahu reports expenses as negative amounts, Sure stores expenses as
    # positive (and renders them with a leading minus in the UI). Negating
    # bridges the two so a debit in Akahu lands as a debit in Sure.
    amount = -transaction.get("amount", 0)

    date_string = _akahu_to_sure_date(transaction.get("date"))

    name = (
        transaction.get("merchant_name")
        or transaction.get("description")
        or "Unknown Transaction"
    )

    payload = {
        "transaction": {
            "account_id": sure_account_id,
            "date": date_string,
            "amount": amount,
            "name": name,
            "notes": f"Akahu ID: {transaction.get('_id', '')}",
        }
    }

    response = requests.post(
        sure_url,
        json=payload,
        headers={"X-Api-Key": sure_api_token},
        timeout=SURE_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    logger.info(f"Sure sync success: {name} for ${amount}")
