"""CLI entrypoint for the Home Assistant OS daily scheduler."""

import argparse
import logging
from pathlib import Path
import signal
import sys
import time


stop_requested = False


def signal_handler(sig, frame):
    global stop_requested
    stop_requested = True
    logging.info("Received signal to terminate scheduler. Shutting down gracefully...")


def sleep_interruptibly(seconds):
    remaining = max(0, int(seconds))
    while remaining > 0 and not stop_requested:
        chunk = min(remaining, 60)
        time.sleep(chunk)
        remaining -= chunk


def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(
        description="Run the Home Assistant OS daily Akahu scheduler."
    )
    parser.add_argument(
        "--refresh-time",
        help="Local HH:MM time to request Akahu refresh. Defaults to HA option refresh_time.",
    )
    parser.add_argument(
        "--sync-time",
        help="Local HH:MM time to import into budgets. Defaults to HA option sync_time.",
    )
    parser.add_argument(
        "--schedule-timezone",
        help="IANA timezone for refresh and sync times.",
    )
    parser.add_argument(
        "--state-file",
        help="Path for persisted scheduler state.",
    )
    args = parser.parse_args()

    from modules.config import (
        ALERT_AFTER_HOURS,
        MAPPING_FILE,
        MAPPING_FILE_MUST_PREEXIST,
        NOTIFY_SERVICE,
        REFRESH_TIME,
        SCHEDULE_TIMEZONE,
        SCHEDULER_STATE_FILE,
        SYNC_TIME,
    )
    from modules.haos_scheduler import SchedulerSettings, run_scheduler

    if MAPPING_FILE_MUST_PREEXIST and not Path(MAPPING_FILE).exists():
        print(f"ERROR: Mapping file not found: {MAPPING_FILE}")
        print(
            "Create it with akahu_budget_mapping.py outside HAOS, then place it at "
            "the configured path, or set ACTUAL_ACCOUNT_LINKS=actual to let Actual "
            "manage the Actual account links."
        )
        return 1

    settings = SchedulerSettings(
        schedule_timezone=args.schedule_timezone or SCHEDULE_TIMEZONE,
        refresh_time=args.refresh_time or REFRESH_TIME,
        sync_time=args.sync_time or SYNC_TIME,
        state_file=args.state_file or SCHEDULER_STATE_FILE,
        alert_after_hours=ALERT_AFTER_HOURS,
        notify_service=NOTIFY_SERVICE,
    )
    run_scheduler(
        settings,
        sleeper=sleep_interruptibly,
        stop_requested=lambda: stop_requested,
    )


if __name__ == "__main__":
    sys.exit(main())
