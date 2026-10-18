"""Home Assistant OS daily scheduler for Akahu refresh and budget sync."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, time, timedelta
import json
import logging
import math
from pathlib import Path
import time as time_module
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from modules import ha_notify
from modules.sync_runner import configure_logging, refresh_akahu, run_sync


LAST_REFRESH_DATE = "last_refresh_requested_local_date"
LAST_REFRESH_AT = "last_refresh_requested_at"
LAST_SYNC_DATE = "last_sync_completed_local_date"
LAST_SYNC_AT = "last_sync_completed_at"
LAST_SYNC_ERROR = "last_sync_error"
LAST_SYNC_FAILED_AT = "last_sync_failed_at"
LAST_ALERT_AT = "last_alert_at"
FIRST_STARTED_AT = "scheduler_first_started_at"

MAX_SLEEP_SECONDS = 3600
REALERT_INTERVAL = timedelta(hours=24)


@dataclass(frozen=True)
class SchedulerSettings:
    schedule_timezone: str
    refresh_time: str
    sync_time: str
    state_file: str
    retry_refresh_seconds: int = 900
    retry_sync_seconds: int = 3600
    alert_after_hours: int = 36
    notify_service: str | None = None

    @property
    def zone(self):
        try:
            return ZoneInfo(self.schedule_timezone)
        except ZoneInfoNotFoundError as e:
            raise EnvironmentError(
                f"Unknown schedule timezone: {self.schedule_timezone}"
            ) from e

    @property
    def refresh_clock(self):
        return parse_hhmm(self.refresh_time, "refresh_time")

    @property
    def sync_clock(self):
        return parse_hhmm(self.sync_time, "sync_time")

    @property
    def refresh_to_sync_gap(self):
        today = date(2000, 1, 1)
        refresh_dt = datetime.combine(today, self.refresh_clock)
        sync_dt = datetime.combine(today, self.sync_clock)
        if sync_dt > refresh_dt:
            return sync_dt - refresh_dt
        return timedelta(hours=1)


@dataclass(frozen=True)
class SchedulerDecision:
    action: str
    delay_seconds: int
    reason: str


def parse_hhmm(value, key):
    parts = str(value).strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"{key} must use HH:MM format")
    hour = int(parts[0])
    minute = int(parts[1])
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"{key} must use HH:MM format")
    return time(hour=hour, minute=minute)


def read_state(path):
    state_path = Path(path)
    if not state_path.exists():
        return {}
    try:
        with state_path.open("r", encoding="utf-8") as f:
            state = json.load(f)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid scheduler state file {state_path}: {e}") from e
    if not isinstance(state, dict):
        raise RuntimeError(f"Scheduler state file {state_path} must contain an object")
    return state


def write_state(path, state):
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def seconds_until(target, now):
    return max(0, math.ceil((target - now).total_seconds()))


def local_day(now):
    return now.date().isoformat()


def at_local(day, clock, zone):
    return datetime.combine(day, clock, tzinfo=zone)


def parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def decide_next_action(now, state, settings):
    zone = settings.zone
    if now.tzinfo is None:
        now = now.replace(tzinfo=zone)
    else:
        now = now.astimezone(zone)

    today = now.date()
    today_key = today.isoformat()
    tomorrow = today + timedelta(days=1)

    refresh_at = at_local(today, settings.refresh_clock, zone)
    next_refresh_at = at_local(tomorrow, settings.refresh_clock, zone)

    if state.get(LAST_SYNC_DATE) == today_key:
        return SchedulerDecision(
            "sleep",
            seconds_until(next_refresh_at, now),
            "sync already completed today",
        )

    if state.get(LAST_REFRESH_DATE) != today_key:
        if now >= refresh_at:
            return SchedulerDecision("refresh", 0, "daily refresh is due")
        return SchedulerDecision(
            "sleep", seconds_until(refresh_at, now), "waiting for daily refresh time"
        )

    sync_at = at_local(today, settings.sync_clock, zone)
    last_refresh_at = parse_datetime(state.get(LAST_REFRESH_AT))
    if last_refresh_at is not None:
        last_refresh_at = last_refresh_at.astimezone(zone)
        sync_at = max(sync_at, last_refresh_at + settings.refresh_to_sync_gap)

    if now >= sync_at:
        return SchedulerDecision("sync", 0, "daily sync is due")
    return SchedulerDecision(
        "sleep", seconds_until(sync_at, now), "waiting for daily sync time"
    )


def mark_refresh_complete(state, now):
    updated = dict(state)
    updated[LAST_REFRESH_DATE] = local_day(now)
    updated[LAST_REFRESH_AT] = now.isoformat()
    return updated


def mark_sync_complete(state, now, finished_at=None):
    updated = dict(state)
    updated[LAST_SYNC_DATE] = local_day(now)
    updated[LAST_SYNC_AT] = (finished_at or now).isoformat()
    updated.pop(LAST_SYNC_ERROR, None)
    updated.pop(LAST_SYNC_FAILED_AT, None)
    return updated


def mark_sync_failed(state, now, error):
    updated = dict(state)
    updated[LAST_SYNC_ERROR] = str(error)
    updated[LAST_SYNC_FAILED_AT] = now.isoformat()
    return updated


def stale_alert_message(now, state, alert_after_hours):
    """Return an alert message if no sync has succeeded recently, else None."""
    last_success = parse_datetime(state.get(LAST_SYNC_AT))
    reference = last_success or parse_datetime(state.get(FIRST_STARTED_AT))
    if reference is None:
        return None
    age = now - reference
    if age < timedelta(hours=alert_after_hours):
        return None
    last_alert = parse_datetime(state.get(LAST_ALERT_AT))
    if last_alert is not None and now - last_alert < REALERT_INTERVAL:
        return None

    hours = int(age.total_seconds() // 3600)
    if last_success is not None:
        message = (
            f"No successful budget sync for {hours} hours "
            f"(last success {last_success.isoformat(timespec='minutes')})."
        )
    else:
        message = (
            f"No successful budget sync in the {hours} hours since the add-on started."
        )
    if state.get(LAST_SYNC_ERROR):
        message += (
            f" Last error at {state.get(LAST_SYNC_FAILED_AT)}: {state[LAST_SYNC_ERROR]}"
        )
    else:
        message += " No failure is recorded; check the add-on log."
    return message


def run_scheduler(
    settings,
    *,
    now_fn=None,
    sleeper=None,
    refresh_fn=None,
    sync_fn=None,
    stop_requested=None,
    notifier=None,
):
    configure_logging()
    now_fn = now_fn or (lambda: datetime.now(settings.zone))
    sleeper = sleeper or time_module.sleep
    refresh_fn = refresh_fn or refresh_akahu
    sync_fn = sync_fn or (lambda: run_sync(skip_akahu_refresh=True))
    stop_requested = stop_requested or (lambda: False)
    notifier = notifier or ha_notify

    logging.info(
        "Starting HAOS scheduler: refresh %s, sync %s, timezone %s, state %s",
        settings.refresh_time,
        settings.sync_time,
        settings.schedule_timezone,
        settings.state_file,
    )

    state = read_state(settings.state_file)
    if FIRST_STARTED_AT not in state:
        write_state(
            settings.state_file,
            {**state, FIRST_STARTED_AT: now_fn().astimezone(settings.zone).isoformat()},
        )

    while not stop_requested():
        state = read_state(settings.state_file)
        now = now_fn().astimezone(settings.zone)

        message = stale_alert_message(now, state, settings.alert_after_hours)
        if message:
            logging.error(message)
            if notifier.alert(
                ha_notify.ALERT_TITLE, message, settings.notify_service
            ):
                state = {**state, LAST_ALERT_AT: now.isoformat()}
                write_state(settings.state_file, state)

        decision = decide_next_action(now, state, settings)

        if decision.action == "sleep":
            # Wake at least hourly so the staleness check keeps running.
            delay = min(decision.delay_seconds, MAX_SLEEP_SECONDS)
            logging.info("Scheduler sleeping %ss: %s", delay, decision.reason)
            sleeper(delay)
            continue

        if decision.action == "refresh":
            logging.info("Running scheduled Akahu refresh: %s", decision.reason)
            try:
                refresh_fn()
            except Exception as e:
                logging.warning(
                    "Scheduled Akahu refresh failed; retrying in %ss: %s",
                    settings.retry_refresh_seconds,
                    e,
                )
                write_state(
                    settings.state_file,
                    mark_sync_failed(state, now, f"Akahu refresh failed: {e}"),
                )
                sleeper(settings.retry_refresh_seconds)
                continue
            write_state(settings.state_file, mark_refresh_complete(state, now))
            continue

        if decision.action == "sync":
            logging.info("Running scheduled budget sync: %s", decision.reason)
            try:
                sync_fn()
            except Exception as e:
                logging.error(
                    "Scheduled budget sync failed; retrying in %ss: %s",
                    settings.retry_sync_seconds,
                    e,
                )
                write_state(settings.state_file, mark_sync_failed(state, now, e))
                sleeper(settings.retry_sync_seconds)
                continue
            updated = mark_sync_complete(state, now, finished_at=now_fn())
            updated.pop(LAST_ALERT_AT, None)
            # Always dismiss: a crash alert from the entry point leaves no state.
            notifier.clear()
            write_state(settings.state_file, updated)
            continue

        raise RuntimeError(f"Unknown scheduler action: {decision.action}")
