"""Tests for the Home Assistant OS daily scheduler decisions."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture(autouse=True)
def _env(clean_env, reload_config):
    clean_env.setenv("RUN_SYNC_TO_YNAB", "true")
    clean_env.setenv("RUN_SYNC_TO_AB", "false")
    clean_env.setenv("AKAHU_USER_TOKEN", "akahu-user")
    clean_env.setenv("AKAHU_APP_TOKEN", "akahu-app")
    clean_env.setenv("YNAB_BEARER_TOKEN", "ynab-bearer")
    reload_config()


@pytest.fixture
def settings(tmp_path):
    from modules.haos_scheduler import SchedulerSettings

    return SchedulerSettings(
        schedule_timezone="Pacific/Auckland",
        refresh_time="04:30",
        sync_time="05:30",
        state_file=str(tmp_path / "state.json"),
    )


def local_dt(hour, minute):
    return datetime(2026, 6, 7, hour, minute, tzinfo=ZoneInfo("Pacific/Auckland"))


def test_waits_until_refresh_time(settings):
    from modules.haos_scheduler import decide_next_action

    decision = decide_next_action(local_dt(4, 0), {}, settings)

    assert decision.action == "sleep"
    assert decision.delay_seconds == 1800
    assert "refresh" in decision.reason


def test_refresh_is_due_once_time_passed(settings):
    from modules.haos_scheduler import decide_next_action

    decision = decide_next_action(local_dt(4, 30), {}, settings)

    assert decision.action == "refresh"


def test_restart_after_refresh_waits_for_sync_time(settings):
    from modules.haos_scheduler import (
        LAST_REFRESH_AT,
        LAST_REFRESH_DATE,
        decide_next_action,
    )

    state = {
        LAST_REFRESH_DATE: "2026-06-07",
        LAST_REFRESH_AT: local_dt(4, 30).isoformat(),
    }

    decision = decide_next_action(local_dt(5, 0), state, settings)

    assert decision.action == "sleep"
    assert decision.delay_seconds == 1800
    assert "sync" in decision.reason


def test_subsecond_wait_before_sync_does_not_spin(settings):
    from modules.haos_scheduler import (
        LAST_REFRESH_AT,
        LAST_REFRESH_DATE,
        decide_next_action,
    )

    state = {
        LAST_REFRESH_DATE: "2026-06-07",
        LAST_REFRESH_AT: local_dt(4, 30).isoformat(),
    }

    decision = decide_next_action(
        datetime(
            2026,
            6,
            7,
            5,
            29,
            59,
            900000,
            tzinfo=ZoneInfo("Pacific/Auckland"),
        ),
        state,
        settings,
    )

    assert decision.action == "sleep"
    assert decision.delay_seconds == 1


def test_sync_is_due_after_refresh_gap(settings):
    from modules.haos_scheduler import (
        LAST_REFRESH_AT,
        LAST_REFRESH_DATE,
        decide_next_action,
    )

    state = {
        LAST_REFRESH_DATE: "2026-06-07",
        LAST_REFRESH_AT: local_dt(4, 30).isoformat(),
    }

    decision = decide_next_action(local_dt(5, 30), state, settings)

    assert decision.action == "sync"


def test_late_start_waits_gap_after_immediate_refresh(settings):
    from modules.haos_scheduler import decide_next_action, mark_refresh_complete

    refreshed_state = mark_refresh_complete({}, local_dt(10, 0))

    decision = decide_next_action(local_dt(10, 15), refreshed_state, settings)

    assert decision.action == "sleep"
    assert decision.delay_seconds == 2700


def test_completed_sync_sleeps_until_tomorrow_refresh(settings):
    from modules.haos_scheduler import LAST_SYNC_DATE, decide_next_action

    decision = decide_next_action(
        local_dt(6, 0),
        {LAST_SYNC_DATE: "2026-06-07"},
        settings,
    )

    assert decision.action == "sleep"
    assert decision.delay_seconds == 81000


def test_state_round_trip(tmp_path):
    from modules.haos_scheduler import read_state, write_state

    state_file = tmp_path / "scheduler.json"
    write_state(str(state_file), {"last_sync_completed_local_date": "2026-06-07"})

    assert read_state(str(state_file)) == {
        "last_sync_completed_local_date": "2026-06-07"
    }


class FakeNotifier:
    def __init__(self, delivered=True):
        self.delivered = delivered
        self.alerts = []
        self.clears = 0

    def alert(self, title, message, notify_service=None):
        self.alerts.append((title, message, notify_service))
        return self.delivered

    def clear(self):
        self.clears += 1
        return True


def run_iterations(settings, iterations, *, now, sync_fn=None, notifier=None):
    """Run the scheduler loop a fixed number of times with a frozen clock."""
    from modules.haos_scheduler import run_scheduler

    sleeps = []
    remaining = [iterations]

    def stop_requested():
        remaining[0] -= 1
        return remaining[0] < 0

    run_scheduler(
        settings,
        now_fn=lambda: now,
        sleeper=sleeps.append,
        refresh_fn=lambda: None,
        sync_fn=sync_fn or (lambda: None),
        stop_requested=stop_requested,
        notifier=notifier or FakeNotifier(),
    )
    return sleeps


def test_no_alert_while_last_success_is_recent():
    from modules.haos_scheduler import LAST_SYNC_AT, stale_alert_message

    state = {LAST_SYNC_AT: local_dt(5, 30).isoformat()}

    assert stale_alert_message(local_dt(23, 0), state, 36) is None


def test_alert_once_last_success_is_too_old():
    from datetime import timedelta

    from modules.haos_scheduler import (
        LAST_SYNC_AT,
        LAST_SYNC_ERROR,
        LAST_SYNC_FAILED_AT,
        stale_alert_message,
    )

    now = local_dt(9, 0)
    state = {
        LAST_SYNC_AT: (now - timedelta(hours=40)).isoformat(),
        LAST_SYNC_ERROR: "404 Not Found",
        LAST_SYNC_FAILED_AT: now.isoformat(),
    }

    message = stale_alert_message(now, state, 36)

    assert "40 hours" in message
    assert "404 Not Found" in message


def test_never_succeeded_measures_from_first_start():
    from datetime import timedelta

    from modules.haos_scheduler import FIRST_STARTED_AT, stale_alert_message

    now = local_dt(9, 0)
    state = {FIRST_STARTED_AT: (now - timedelta(hours=37)).isoformat()}

    assert "since the add-on started" in stale_alert_message(now, state, 36)
    assert stale_alert_message(now, {}, 36) is None


def test_realert_waits_24_hours():
    from datetime import timedelta

    from modules.haos_scheduler import LAST_ALERT_AT, LAST_SYNC_AT, stale_alert_message

    now = local_dt(9, 0)
    state = {
        LAST_SYNC_AT: (now - timedelta(days=5)).isoformat(),
        LAST_ALERT_AT: (now - timedelta(hours=23)).isoformat(),
    }
    assert stale_alert_message(now, state, 36) is None

    state[LAST_ALERT_AT] = (now - timedelta(hours=25)).isoformat()
    assert stale_alert_message(now, state, 36) is not None


def test_stale_state_alerts_and_records_alert(settings):
    from datetime import timedelta

    from modules.haos_scheduler import LAST_ALERT_AT, LAST_SYNC_AT, read_state

    now = local_dt(4, 0)
    write_state_file(settings, {LAST_SYNC_AT: (now - timedelta(days=3)).isoformat()})
    notifier = FakeNotifier()

    run_iterations(settings, 1, now=now, notifier=notifier)

    assert len(notifier.alerts) == 1
    assert read_state(settings.state_file)[LAST_ALERT_AT] == now.isoformat()


def test_undelivered_alert_is_retried_next_time(settings):
    from datetime import timedelta

    from modules.haos_scheduler import LAST_ALERT_AT, LAST_SYNC_AT, read_state

    now = local_dt(4, 0)
    write_state_file(settings, {LAST_SYNC_AT: (now - timedelta(days=3)).isoformat()})

    run_iterations(settings, 1, now=now, notifier=FakeNotifier(delivered=False))

    assert LAST_ALERT_AT not in read_state(settings.state_file)


def test_successful_sync_clears_active_alert(settings):
    from datetime import timedelta

    from modules.haos_scheduler import (
        LAST_ALERT_AT,
        LAST_REFRESH_AT,
        LAST_REFRESH_DATE,
        LAST_SYNC_AT,
        LAST_SYNC_ERROR,
        read_state,
    )

    now = local_dt(6, 0)
    write_state_file(settings, {
        LAST_REFRESH_DATE: "2026-06-07",
        LAST_REFRESH_AT: local_dt(4, 30).isoformat(),
        LAST_SYNC_AT: (now - timedelta(days=3)).isoformat(),
        LAST_SYNC_ERROR: "boom",
        LAST_ALERT_AT: (now - timedelta(hours=1)).isoformat(),
    })
    notifier = FakeNotifier()

    run_iterations(settings, 1, now=now, notifier=notifier)

    state = read_state(settings.state_file)
    assert notifier.clears == 1
    assert LAST_ALERT_AT not in state
    assert LAST_SYNC_ERROR not in state
    assert state[LAST_SYNC_AT] == now.isoformat()


def test_failed_sync_records_error(settings):
    from modules.haos_scheduler import (
        LAST_REFRESH_AT,
        LAST_REFRESH_DATE,
        LAST_SYNC_ERROR,
        LAST_SYNC_FAILED_AT,
        read_state,
    )

    now = local_dt(6, 0)
    write_state_file(settings, {
        LAST_REFRESH_DATE: "2026-06-07",
        LAST_REFRESH_AT: local_dt(4, 30).isoformat(),
    })

    def failing_sync():
        raise RuntimeError("Akahu 404")

    run_iterations(settings, 1, now=now, sync_fn=failing_sync)

    state = read_state(settings.state_file)
    assert state[LAST_SYNC_ERROR] == "Akahu 404"
    assert state[LAST_SYNC_FAILED_AT] == now.isoformat()


def test_sleep_is_capped_so_staleness_is_checked_hourly(settings):
    from modules.haos_scheduler import LAST_SYNC_AT, LAST_SYNC_DATE, MAX_SLEEP_SECONDS

    now = local_dt(6, 0)
    write_state_file(settings, {
        LAST_SYNC_DATE: "2026-06-07",
        LAST_SYNC_AT: now.isoformat(),
    })

    sleeps = run_iterations(settings, 1, now=now)

    assert sleeps == [MAX_SLEEP_SECONDS]


def test_first_start_is_recorded(settings):
    from modules.haos_scheduler import FIRST_STARTED_AT, LAST_SYNC_DATE, read_state

    now = local_dt(6, 0)
    write_state_file(settings, {LAST_SYNC_DATE: "2026-06-07"})

    run_iterations(settings, 0, now=now)

    assert read_state(settings.state_file)[FIRST_STARTED_AT] == now.isoformat()


def write_state_file(settings, state):
    from modules.haos_scheduler import write_state

    write_state(settings.state_file, state)
