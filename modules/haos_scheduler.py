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

from modules.sync_runner import configure_logging, refresh_akahu, run_sync


LAST_REFRESH_DATE = "last_refresh_requested_local_date"
LAST_REFRESH_AT = "last_refresh_requested_at"
LAST_SYNC_DATE = "last_sync_completed_local_date"


@dataclass(frozen=True)
class SchedulerSettings:
    schedule_timezone: str
    refresh_time: str
    sync_time: str
    state_file: str
    retry_refresh_seconds: int = 900
    retry_sync_seconds: int = 3600

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


def mark_sync_complete(state, now):
    updated = dict(state)
    updated[LAST_SYNC_DATE] = local_day(now)
    updated["last_sync_completed_at"] = now.isoformat()
    return updated


def run_scheduler(
    settings,
    *,
    now_fn=None,
    sleeper=None,
    refresh_fn=None,
    sync_fn=None,
    stop_requested=None,
):
    configure_logging()
    now_fn = now_fn or (lambda: datetime.now(settings.zone))
    sleeper = sleeper or time_module.sleep
    refresh_fn = refresh_fn or refresh_akahu
    sync_fn = sync_fn or (lambda: run_sync(skip_akahu_refresh=True))
    stop_requested = stop_requested or (lambda: False)

    logging.info(
        "Starting HAOS scheduler: refresh %s, sync %s, timezone %s, state %s",
        settings.refresh_time,
        settings.sync_time,
        settings.schedule_timezone,
        settings.state_file,
    )

    while not stop_requested():
        state = read_state(settings.state_file)
        now = now_fn().astimezone(settings.zone)
        decision = decide_next_action(now, state, settings)

        if decision.action == "sleep":
            logging.info(
                "Scheduler sleeping %ss: %s",
                decision.delay_seconds,
                decision.reason,
            )
            sleeper(decision.delay_seconds)
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
                sleeper(settings.retry_refresh_seconds)
                continue
            write_state(settings.state_file, mark_refresh_complete(state, now))
            continue

        if decision.action == "sync":
            logging.info("Running scheduled budget sync: %s", decision.reason)
            try:
                sync_fn()
            except Exception as e:
                logging.warning(
                    "Scheduled budget sync failed; retrying in %ss: %s",
                    settings.retry_sync_seconds,
                    e,
                )
                sleeper(settings.retry_sync_seconds)
                continue
            write_state(settings.state_file, mark_sync_complete(state, now))
            continue

        raise RuntimeError(f"Unknown scheduler action: {decision.action}")
