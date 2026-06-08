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
