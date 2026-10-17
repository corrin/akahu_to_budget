# Akahu to Budget add-on

> **Note for Actual Budget users:** Actual Budget has upstream Akahu bank sync
> (<https://actualbudget.org/docs/advanced/bank-sync/akahu/>), but it only runs
> while Actual is open. To have this add-on sync daily using the links you made
> in Actual's Bank Sync page, set `ACTUAL_ACCOUNT_LINKS: actual` (see below).

This add-on is a personal Home Assistant OS wrapper around the existing
`akahu_to_budget` sync code. It is intentionally scheduled daily by default
because Akahu is synced daily in Corrin's setup. Other deployments should keep
using local Python, Docker/Podman, cron, systemd, or the existing web path.

The add-on pulls `ghcr.io/corrin/akahu_to_budget-haos`, which is built from the
repo root so this wrapper does not carry a separate copy of the sync code.

## Setup

1. Generate `akahu_budget_mapping.json` outside Home Assistant with the normal
   `python akahu_budget_mapping.py` workflow.
2. Place that file where the add-on can read it.
   - For normal UI setup, copy it into this add-on's config directory with
     Studio Code Server, File Editor, or another Home Assistant file tool, then
     leave `mapping_file` as `/config/akahu_budget_mapping.json`.
   - For automation or MCP-driven setup, paste a one-time copy into
     `mapping_json`:

     ```yaml
     mapping_json: |-
       {
         "akahu_accounts": {},
         "actual_accounts": {},
         "ynab_accounts": {},
         "mapping": {}
       }
     ```

     The add-on writes this value to `mapping_file` only if the mapping file is
     missing. Clear `mapping_json` after the first successful start.
3. Fill in the add-on options for Akahu and the enabled budget target.
4. Start the add-on and check its log in Home Assistant. It should print the
   options file, mapping file, refresh time, and sync time before the scheduler
   starts.

## Let Actual manage account links

With `ACTUAL_ACCOUNT_LINKS: actual`, link each Actual account to its Akahu
account in Actual's Bank Sync page. Each sync regenerates the Actual links in
`mapping_file` from Actual, so no hand-made mapping is needed unless YNAB or Sure
is also enabled. Off-budget accounts are balance-matched (`Tracking`); on-budget
accounts get transactions imported. A link to an Akahu account that no longer
exists fails the sync loudly; relink it in Actual.

## Alerts

If no scheduled sync has succeeded for `alert_after_hours` (default `36`), the
add-on raises a Home Assistant notification with the last error, repeated at most
daily and dismissed by the next successful sync. Set `notify_service` (for
example `notify.mobile_app_my_phone`) to also send it to a phone. The add-on
cannot alert while it is stopped, so turn on its watchdog.

## Schedule

The add-on asks Akahu to refresh connected accounts once per local day, then
imports into the enabled budget target later the same morning:

- `refresh_time`: defaults to `04:30`
- `sync_time`: defaults to `05:30`
- `schedule_timezone`: defaults to `Pacific/Auckland`

The scheduler persists its state in `scheduler_state_file`, which defaults to
`/config/akahu_to_budget_state.json`. If Home Assistant restarts after a sync
has completed, the add-on will not run the same daily sync again.

Set `log_file` to an empty string for Supervisor-only logging. That is the
default for this add-on.

Sure Finance sidecar mode needs Docker/Podman access to the Sure Rails
container. In most Home Assistant OS installs, leave `SURE_USE_SIDECAR` disabled
and use the Sure HTTP API options instead.
