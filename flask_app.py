import os
import logging
import argparse
import signal
import sys

from modules.mapping_store import load_existing_mapping
from modules.config import AKAHU_ENDPOINT, AKAHU_HEADERS, RUN_SYNC_TO_AB
from modules.sync_runner import configure_logging, get_actual_client, run_sync
from modules.webhook_handler import create_flask_app
import sure_client

# actualpy is an optional dependency; modules.config raises at import time if
# RUN_SYNC_TO_AB=true and it's missing. Importing here is unconditional because
# get_actual_client() guards on RUN_SYNC_TO_AB before constructing the client.
if RUN_SYNC_TO_AB:
    from actual import Actual


# Create and export the Flask app for WSGI
def signal_handler(sig, frame):
    logging.info("Received signal to terminate. Shutting down gracefully...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)  # Handle Ctrl+C
signal.signal(signal.SIGTERM, signal_handler)  # Handle kill


def create_application():
    """Create Flask application."""
    _, _, _, mapping_list = load_existing_mapping()
    
    with get_actual_client() as actual_client:
        app = create_flask_app(actual_client, mapping_list, {
            'AKAHU_PUBLIC_KEY': os.getenv('AKAHU_PUBLIC_KEY', ''),  # RFU (Reserved For Future Use)
            'akahu_endpoint': AKAHU_ENDPOINT,
            'akahu_headers': AKAHU_HEADERS
        })
        return app


# Create and expose the Flask application for WSGI if not running in sync mode
configure_logging()
application = None
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Flask app or perform direct sync.")
    parser.add_argument("--sync", action="store_true", help="Perform direct sync and exit.")
    parser.add_argument("--accounts", help="Comma-separated list of Akahu account IDs to sync (e.g. acc_123,acc_456). If not provided, all accounts will be synced.")
    parser.add_argument("--debug", nargs='?', const='all', help="Enable debug mode. Without parameter, prints Akahu IDs for all transactions. With parameter, treats it as an Akahu transaction ID and enables verbose debugging for that transaction.")
    args = parser.parse_args()

    if args.sync:
        logging.warning(
            "python flask_app.py --sync is deprecated and may be removed in a future version. "
            "Use python sync_cli.py instead."
        )
        account_ids = args.accounts.split(',') if args.accounts else None
        run_sync(account_ids, debug_mode=args.debug)
    else:
        application = create_application()
        development_mode = os.getenv('FLASK_ENV') == 'development'
        application.run(host="0.0.0.0", port=5000, debug=development_mode)
else:
    # For WSGI deployment, create the application
    application = create_application()
