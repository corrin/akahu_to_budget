"""Module for handling webhook operations and Flask app creation."""

import base64
import logging
from flask import Flask, request, jsonify, redirect, url_for
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
import pandas as pd

from modules.account_mapper import load_existing_mapping
from modules.config import RUN_SYNC_TO_AB, RUN_SYNC_TO_YNAB, YNAB_ENDPOINT, YNAB_HEADERS
from modules.sync_handler import sync_to_ab, sync_to_ynab
from modules.sync_status import generate_sync_report
from modules.transaction_handler import (
    load_transactions_into_actual,
    clean_txn_for_ynab,
    load_transactions_into_ynab,
    create_adjustment_txn_ynab,
)
from modules.account_fetcher import (
    get_akahu_balance,
    get_ynab_balance,
    trigger_akahu_refresh,
)
from modules.transaction_tester import run_transaction_tests


def verify_signature(public_key: str, signature: str, request_body: bytes) -> None:
    """Verify that the request body has been signed by Akahu."""
    public_key = serialization.load_pem_public_key(public_key.encode("utf-8"))
    public_key.verify(
        base64.b64decode(signature), request_body, padding.PKCS1v15(), hashes.SHA256()
    )


def create_flask_app(actual_client, mapping_list, env_vars):
    """Create and configure Flask application for webhook handling."""
    app = Flask(__name__)

    @app.route("/test", methods=["GET"])
    def test_transactions():
        """Test endpoint to validate transaction handling."""
        try:
            result = run_transaction_tests(actual_client, mapping_list, env_vars)
            return jsonify(result), 200
        except Exception as e:
            logging.error(f"\n=== Test Failed ===\nError in test endpoint: {str(e)}")
            return jsonify({"error": str(e)}), 500

    @app.route("/")
    def root():
        """Root endpoint shows deprecation notice and status."""
        if "akahu_to_budget.py" in sys.argv[0]:
            notice = """
            <h1>⚠️ Deprecation Notice</h1>
            <p>This script (akahu_to_budget.py) is deprecated in favor of flask_app.py</p>
            <p>While this script still works, flask_app.py provides additional features:</p>
            <ul>
                <li>CLI sync support (python flask_app.py --sync)</li>
                <li>Better error handling</li>
                <li>Signal handling for graceful shutdown</li>
            </ul>
            <hr>
            """
            return notice + redirect(url_for("status")).get_data(as_text=True)
        return redirect(url_for("status"))

    @app.route("/sync", methods=["GET"])
    def run_full_sync():
        """Run a full sync of all accounts."""
        errors = []
        actual_count = 0
        ynab_count = 0

        try:
            trigger_akahu_refresh()

            _, _, _, mapping_list = load_existing_mapping()

            if RUN_SYNC_TO_AB:
                actual_client.download_budget()
                actual_count = sync_to_ab(actual_client, mapping_list)

            if RUN_SYNC_TO_YNAB:
                ynab_count = sync_to_ynab(mapping_list)

            return generate_sync_report(mapping_list, actual_count, ynab_count)

        except Exception as e:
            logging.error(f"Sync failed: {str(e)}")
            return (
                jsonify(
                    {
                        "status": "error",
                        "error": str(e),
                    }
                ),
                500,
            )

    @app.route("/status", methods=["GET"])
    def status():
        """Endpoint to check if the webhook server is running."""
        return jsonify({"status": "Webhook server is running"}), 200

    @app.route("/receive-transaction", methods=["POST"])
    def receive_transaction():
        """Handle incoming webhook events from Akahu.
        Note: This endpoint is RFU (Reserved For Future Use) pending security audit and proper
        webhook authentication implementation."""
        signature = request.headers.get("X-Akahu-Signature")
        verify_signature(env_vars["AKAHU_PUBLIC_KEY"], signature, request.data)

        data = request.get_json()
        if data["type"] != "TRANSACTION_CREATED":
            return jsonify({"status": "ignored - not a transaction event"}), 200

        transactions = data["item"]
        akahu_account_id = transactions["account"]["_id"]
        mapping_entry = mapping_list[akahu_account_id]

        # Process for Actual Budget if enabled
        if RUN_SYNC_TO_AB and not mapping_entry.get("actual_do_not_map"):
            actual_client.download_budget()
            load_transactions_into_actual(
                pd.DataFrame([transactions]), mapping_entry, actual_client
            )

        # Process for YNAB if enabled
        if RUN_SYNC_TO_YNAB and not mapping_entry.get("ynab_do_not_map"):
            if mapping_entry.get("account_type") == "Tracking":
                # For tracking accounts, create balance adjustment
                akahu_balance = get_akahu_balance(
                    akahu_account_id,
                    env_vars["akahu_endpoint"],
                    env_vars["akahu_headers"],
                )
                if akahu_balance is not None:
                    akahu_balance_milliunits = int(akahu_balance * 1000)
                    ynab_balance_milliunits = get_ynab_balance(
                        mapping_entry["ynab_budget_id"],
                        mapping_entry["ynab_account_id"],
                    )
                    if ynab_balance_milliunits != akahu_balance_milliunits:
                        create_adjustment_txn_ynab(
                            mapping_entry["ynab_budget_id"],
                            mapping_entry["ynab_account_id"],
                            akahu_balance_milliunits,
                            ynab_balance_milliunits,
                            YNAB_ENDPOINT,
                            YNAB_HEADERS,
                        )
            else:
                # For regular accounts, process the transaction
                df = pd.DataFrame([transactions])
                cleaned_txn = clean_txn_for_ynab(df, mapping_entry["ynab_account_id"])
                load_transactions_into_ynab(
                    cleaned_txn,
                    mapping_entry["ynab_budget_id"],
                    mapping_entry["ynab_account_id"],
                    YNAB_ENDPOINT,
                    YNAB_HEADERS,
                )

        return jsonify({"status": "success"}), 200

    return app


def start_webhook_server(app, development_mode=False):
    """Start the Flask webhook server."""
    app.run(host="0.0.0.0", port=5000, debug=development_mode)
