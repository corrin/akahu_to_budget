# Akahu to Budget add-on

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
