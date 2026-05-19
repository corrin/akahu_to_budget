"""CLI entrypoint for one-shot sync without importing Flask."""

import argparse
import logging
import os
import signal
import sys


def set_env_override(key, value):
    """Apply a CLI override without treating empty strings as missing values."""
    if value is None:
        return
    os.environ[key] = value


def signal_handler(sig, frame):
    logging.info("Received signal to terminate. Shutting down gracefully...")
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(description="Perform direct sync.")
    parser.add_argument(
        "--accounts",
        help=(
            "Comma-separated list of Akahu account IDs to sync "
            "(e.g. acc_123,acc_456). If not provided, all accounts will be synced."
        ),
    )
    parser.add_argument(
        "--debug",
        nargs="?",
        const="all",
        help=(
            "Enable debug mode. Without parameter, prints Akahu IDs for all "
            "transactions. With parameter, treats it as an Akahu transaction ID "
            "and enables verbose debugging for that transaction."
        ),
    )
    parser.add_argument(
        "--mapping-file",
        help="Path to akahu_budget_mapping.json. Defaults to MAPPING_FILE or the local file.",
    )
    parser.add_argument(
        "--log-file",
        help=(
            "Path for file logging. Defaults to LOG_FILE or app.log. "
            "Use an empty string to log to stdout only."
        ),
    )
    args = parser.parse_args()

    set_env_override("MAPPING_FILE", args.mapping_file)
    set_env_override("LOG_FILE", args.log_file)

    from modules.sync_runner import configure_logging, run_sync

    configure_logging()

    account_ids = args.accounts.split(",") if args.accounts else None
    run_sync(account_ids, debug_mode=args.debug)


if __name__ == "__main__":
    main()
