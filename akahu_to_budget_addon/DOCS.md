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
2. Copy that file into this add-on's config directory, or set `mapping_file` to
   the path where you placed it.
3. Fill in the add-on options for Akahu and the enabled budget target.
4. Start the add-on and watch the Supervisor log.

Set `log_file` to an empty string for Supervisor-only logging. That is the
default for this add-on.
