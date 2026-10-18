# Changelog

## 0.2.0

- Add `ACTUAL_ACCOUNT_LINKS: actual` to let Actual's Bank Sync page manage the
  Akahu-to-Actual account links. The Actual side of the mapping file is
  regenerated from Actual on every sync; YNAB and Sure links are unchanged, and
  the default (`mapping_file`) keeps the existing behaviour.
- Skip and report an Actual account linked to an Akahu account that Akahu no
  longer returns as active (such as after reconnecting a bank in Akahu). The
  remaining accounts still sync, and the run is marked failed.
- Raise a Home Assistant notification when no scheduled sync has succeeded for
  `alert_after_hours` (default 36), optionally also via `notify_service`.
- Record the last sync error and the actual sync finish time in the scheduler
  state file.
- Raise the same notification if the scheduler itself stops with an error,
  such as a missing mapping file or a corrupt state file.
- Fix `akahu_budget_mapping.py` crashing when a YNAB account was deleted, and
  never unlinking deleted Actual accounts.

## 0.1.3

- Request Akahu refresh at the configured daily refresh time, then import into
  the enabled budget target at the configured sync time.
- Persist scheduler state in the add-on config directory so restarts do not
  trigger duplicate same-day syncs.
- Add CLI flags for refresh-only and import-without-refresh runs.

## 0.1.2

- Add Home Assistant add-on changelog metadata so the app store can display
  release notes.
- Clarify the add-on setup documentation around checking startup logs.

## 0.1.1

- Add a `mapping_json` option for one-time copy/paste or MCP-driven mapping
  bootstrap.
- Keep `mapping_json` as raw JSON and write it to `mapping_file` only when the
  mapping file is missing.
- Document the HAOS mapping setup paths.

## 0.1.0

- Add the initial Home Assistant OS add-on wrapper.
- Publish the HAOS image as `ghcr.io/corrin/akahu_to_budget-haos`.
