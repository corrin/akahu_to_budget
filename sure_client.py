"""Push Akahu transactions to a self-hosted Sure Finance instance."""

import logging
import os
import json
import subprocess
import shutil
import zoneinfo
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Pull configuration at call time, not import time, so test harnesses and the
# config validation in modules.config can reliably see the values.
SURE_DEFAULT_URL = "http://127.0.0.1:8084/api/v1/transactions"
SURE_REQUEST_TIMEOUT_SECONDS = 15
NZ_TIMEZONE = zoneinfo.ZoneInfo("Pacific/Auckland")

def _setting(name, default=None):
    from modules.config import ENVs

    value = ENVs.get(name)
    if value is not None:
        return value
    return os.environ.get(name, default)


def _bool_setting(name, default=False):
    value = _setting(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


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


def push_transactions(transactions, sure_account_id):
    """
    Main entry point. Routes to either the Sidecar (batch) or API (loop) 
    based on the USE_SIDECAR toggle.
    """
    if not transactions:
        return 0

    if _bool_setting("SURE_USE_SIDECAR", default=True):
        return _push_via_sidecar(transactions, sure_account_id)
    else:
        # Fallback to the original method: looping over the API one by one
        success_count = 0
        for txn in transactions:
            _push_via_api(txn, sure_account_id)
            success_count += 1
        return success_count


def _push_via_api(transaction, sure_account_id):
    """The ORIGINAL HTTP API method: Post a single Akahu transaction dict to Sure Finance."""
    sure_api_token = _setting("SURE_API_TOKEN")
    if not sure_api_token:
        raise RuntimeError("SURE_API_TOKEN is missing. Is RUN_SYNC_TO_SURE set correctly?")

    sure_url = _setting("SURE_API_URL", SURE_DEFAULT_URL)

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
            "external_id": transaction.get("_id", ""), # Ready for when the API is fixed!
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


def _push_via_sidecar(transactions, sure_account_id):
    """The TEMPORARY Batch Docker method."""
    payload_txns = []
    for t in transactions:
        payload_txns.append({
            "date": _akahu_to_sure_date(t.get("date")),
            "amount": -t.get("amount", 0),
            "name": t.get("merchant_name") or t.get("description") or "Unknown Transaction",
            "external_id": t.get("_id", "")
        })
    
    payload = {"account_id": sure_account_id, "transactions": payload_txns}
    json_data = json.dumps(payload)

    # We template the JSON directly into the Ruby script and pass the whole thing via stdin.
    # Note: Double braces {{ }} are used to escape Python's f-string formatting for Ruby interpolation.
    ruby_code = f"""
require 'json'

payload = JSON.parse(<<~'JSON_PAYLOAD'
{json_data}
JSON_PAYLOAD
)

account = Account.find(payload['account_id'])
created_count = 0

payload['transactions'].each do |txn|
  entry = account.entries.find_or_initialize_by(
    external_id: txn['external_id'],
    source: "akahu"
  )

  if entry.new_record?
    entry.assign_attributes(
      date: txn['date'],
      amount: txn['amount'],
      name: txn['name'],
      currency: "NZD",
      entryable_type: "Transaction"
    )
    
    # The 'nature' attribute is no longer used for Transaction in recent versions
    entry.build_entryable unless entry.entryable
    
    entry.save!
    created_count += 1
    puts " -> Created: #{{txn['name']}} (#{{txn['external_id']}})"
  else
    puts " -> Skipped (already exists): #{{txn['external_id']}}"
  end
end

puts "SUCCESS: Imported #{{created_count}} new transactions."
"""

    runtime = _setting("SURE_CONTAINER_RUNTIME")
    if not runtime:
        runtime = shutil.which("podman") or shutil.which("docker")
        
    if not runtime:
        raise RuntimeError(
            "Neither podman nor docker found in PATH. "
            "If you are running via systemd/cron, try setting SURE_CONTAINER_RUNTIME in your .env"
        )

    container_name = _setting("SURE_CONTAINER_NAME", "sure-core")

    # Pass "-" to rails runner so it reads our templated string from stdin
    cmd = [runtime, "exec", "-i", container_name, "bin/rails", "runner", "-"]
    logger.info(f"Executing batch push of {len(transactions)} transactions via {runtime} to {container_name}...")
    
    # We encode the string to bytes here so it safely pipes into the subprocess stdin
    result = subprocess.run(cmd, input=ruby_code, capture_output=True, text=True)
    
    if result.returncode != 0:
        logger.error(f"Sidecar execution failed:\n{result.stderr}")
        raise RuntimeError(f"Rails runner sidecar failed: {result.stderr}")
    
    # Filter out explicitly known noisy Rails boot warnings
    noisy_markers = [
        "[SKYLIGHT]", 
        "[ProviderLoader]", 
        "[OmniAuth]", 
        "No SSO providers"
    ]
    
    for line in result.stdout.splitlines():
        cleaned_line = line.strip()
        if not cleaned_line:
            continue
            
        # If any of the noisy markers are in the line, silently skip it
        if any(marker in cleaned_line for marker in noisy_markers):
            continue
            
        logger.info(f"Sure DB: {cleaned_line}")
            
    return len(transactions)
