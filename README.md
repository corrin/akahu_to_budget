# akahu_to_budget
One-way sync of transactions from Akahu to YNAB, Actual Budget, or Sure Finance.

We support Actual Budget, YNAB, and Sure Finance. You can sync to all of them, or just pick the ones you use.

# Project status

I started writing this script some time ago. As of 2024-12-28... I've finally swapped from using my old hodgepodge version to using this.
That means that today, it's feature complete but not yet battle-hardened. If you're reading this notably after Dec 2024 then it should be pretty robust.

Also in terms of my personal setup, I haven't yet fully committed to giving up YNAB and am vacillating between YNAB and AB. You might see oddities in the data created for AB (e.g. Payees, failing to trigger rules). Please raise bugs.

*(Note: Sure Finance support was added recently for users looking for an alternative self-hosted platform).*

# Setup

1. Create an Akahu account and an Akahu app: [https://my.akahu.nz/login](https://my.akahu.nz/login)
2. Optionally set up an OpenAI account and get an API key for smarter account mapping: [https://platform.openai.com/account/api-keys](https://platform.openai.com/account/api-keys)
3. Set up an Actual Budget Server and get the server URL, password, encryption key, and sync ID: [https://actualbudget.org/](https://actualbudget.org/). I used PikaPods
4. And/OR in YNAB get a bearer token and the budget ID: [https://api.youneedabudget.com/](https://api.youneedabudget.com/)
5. And/OR set up a self-hosted [Sure Finance](https://github.com/we-promise/sure) instance.
6. Check out this repository
7. Create a virtual environment and run `pip install -r requirements.txt`
8. Create a `.env` file in the root of the project. (see [.env.example](./.env.example))

Note that the OPENAI key is optional. I included it more for fun. It makes the matching a bit smarter. In the future I might make it so it tries to guess your category based on the transaction memo.

You can find an example .env file in this repository called .env.example - just rename it to .env

# Setup (detail)

Note that I have both YNAB and AB. In theory this script supports running with any combination of the supported budgeting apps, but it's primarily tested with YNAB and AB. If you run into edge cases, please get in contact.

## Akahu
To sign up to Akahu you need to create what they call personal API or a 'user scoped endpoint' as documented here
https://developers.akahu.nz/reference/api-akahu-io-authentication

Here's a picture from my setup

![Akahu Setup](documentation/akahu_setup.png)

## Actual Budget

I use pikapods for my setup.  You can sign up here: https://actualbudget.org/docs/install/pikapods/

Once you've signed up you can set up your accounts and create your budget. If you're coming from YNAB then there's a tool: https://json-exporter-for-ynab.netlify.app/

I prefer to both have a password for my Actual Budget server AND to encrypt my data on Actual Budget. That way even if someone broke into PikaPods they wouldn't get automatic access to my financial data. The code assumes you're doing this too - you'll need to tweak it

Now open your budget in YNAB and click 'show advanced settings'

![Actual Setup](documentation/actual_budget_settings.png)

## YNAB

You can create a personal API in YNAB as per the instructions https://api.ynab.com/

If you're the type of person who just wants to get up and running as quickly as possible and then circle back to fill in the gaps, these steps are for you:

1. Sign in to the YNAB web app and go to the "Account Settings" page and then to the "Developer Settings" page.
2. Under the "Personal Access Tokens" section, click "New Token", enter your password and click "Generate" to get an access token.
3. Open a terminal window and run this:
`curl -H "Authorization: Bearer <ACCESS_TOKEN>" https://api.ynab.com/v1/budgets`

![YNAB Setup](documentation/ynab_setup.png)

## Sure Finance

Sure Finance is a self-hosted alternative to Maybe Finance. Due to current API limitations regarding transaction deduplication, syncing to Sure Finance requires Docker/Podman access to the host machine so the script can communicate directly with the database. See the **Sure Finance Sync & Deduplication Sidecar** section below for configuration details.

## OpenAI

I haven't bothered to document this because it's optional.  Add the API key.  Have fun.  Currently OpenAI is only used to help mapping accounts. It would make sense to use it mapping transactions too but... that hasn't been done.

## Python

I always use a virtual environment for each project. I used Python 3.12 here, but most versions of Python 3 should work.

```bash
python3.12 -m venv .venv

# Linux/Mac
source .venv/bin/activate

# Windows
.\.venv\Scripts\activate

# Once activated
pip install -r requirements.txt
```

Before preparing account mappings, install the setup dependencies:

```bash
pip install -r requirements_setup.txt
```

If you want to run the webhook server, also install the web dependencies:

```bash
pip install -r requirements_web.txt
```

# Preparing to run the script

Run `python akahu_budget_mapping.py`

This lets you interactively map your bank accounts with accounts set up in Actual Budget, YNAB, or Sure Finance.

It will ask you a bunch of questions like
```Akahu Account: DAY TO DAY (Connection: Kiwibank)
Here is a list of actual accounts:
...
Enter the number corresponding to the best match (or press Enter to skip):
```

Ultimately this will write the file `akahu_budget_mapping.json`.

You will likely never need to run this again unless you want to change the mapping.

# Running the script

Run the script using:
```bash
# For one-time sync (recommended for most users):
python sync_cli.py

# For running the webhook server:
python flask_app.py
```

This connects to Akahu, gets the transactions, and syncs them to Actual Budget and/or YNAB.

When running the webhook server, the first sync is triggered automatically on startup. For subsequent syncs, use the web interface at http://localhost:5000/sync.
There is minimal security, mostly because the webhooks don't take parameters so the worst someone can do is sync your budget prematurely.

NOTE TO EXISTING USERS: If you've been using akahu_to_budget.py, we have finished the migration to `sync_cli.py` for one-time syncs and `flask_app.py` for the webhook server. `python flask_app.py --sync` is deprecated and may be removed in a future version.

# Running in a container

The repository ships a `Containerfile` (works with both `docker` and `podman`)
and publishes an image to GitHub Container Registry on every push to `main`
and on tagged releases:

- `ghcr.io/corrin/akahu_to_budget:latest` — latest `main`
- `ghcr.io/corrin/akahu_to_budget:v1.2.3` — specific tag

You still need to provide your `.env` file and the `akahu_budget_mapping.json`
you generated during setup. Mount them both into the container:

```bash
podman run --rm \
  --env-file ./.env \
  -v ./akahu_budget_mapping.json:/app/akahu_budget_mapping.json \
  ghcr.io/corrin/akahu_to_budget:latest
```

The container image runs one-off syncs and does not include Flask. Use host cron
or a systemd timer for scheduled container syncs.

Substitute `docker` for `podman` if you prefer. To build locally instead of
pulling:

```bash
podman build -t akahu_to_budget:local -f Containerfile .
```

# Running on Home Assistant OS

This repo also includes a Home Assistant OS add-on wrapper in
`akahu_to_budget_addon/`. It is intended for my personal HAOS setup: the add-on
stays running and performs a scheduled daily sync by default because Akahu is
synced daily. That automatic recurring sync is not necessarily the
right behavior for everyone; the normal Python, Docker/Podman, cron, systemd,
and Flask deployment paths remain supported.

The add-on uses the `ghcr.io/corrin/akahu_to_budget-haos` image, built from the
repo root so it shares the same sync code as the other deployment methods.

## HAOS setup

This is a Home Assistant OS app/add-on, not a HACS integration. Install it from
the Home Assistant app store by adding this repository as an app repository.

1. Generate `akahu_budget_mapping.json` before installing the add-on:

   ```bash
   python akahu_budget_mapping.py
   ```

   This is still an interactive setup step and is easier to run on your normal
   computer than inside Home Assistant.

2. Add this repository to Home Assistant:

   [Add Akahu to Budget repository to Home Assistant](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fcorrin%2Fakahu_to_budget)

   If the direct link does not work, add it manually:

   1. In Home Assistant, go to **Settings → Apps**.
   2. Select **App store** or **Install app**.
   3. Open the top-right `...` menu in the app store.
   4. Choose **Repositories**.
   5. Add this repository URL:

   ```text
   https://github.com/corrin/akahu_to_budget
   ```

3. Install the **Akahu to Budget** app.
4. Place `akahu_budget_mapping.json` where the add-on can read it.

   For most people, the clearest option is to copy the file into this add-on's
   config directory with a Home Assistant file tool such as Studio Code Server
   or File Editor, then leave the default add-on option as:

   ```text
   /config/akahu_budget_mapping.json
   ```

   If you place the file somewhere else, update the `mapping_file` option to
   match that path.

   For automation or MCP-driven setup, you can instead paste a one-time copy
   into the `mapping_json` option. In the YAML options editor, a block scalar
   is easiest:

   ```yaml
   mapping_json: |-
     {
       "akahu_accounts": {},
       "actual_accounts": {},
       "ynab_accounts": {},
       "mapping": {}
     }
   ```

   On startup, the add-on writes that value to `mapping_file` only if the
   mapping file is missing. After the first successful start, clear
   `mapping_json` from the add-on options so the mapping is not stored in
   Supervisor options longer than necessary.

5. Fill in the app options for the services you use:

   - `RUN_SYNC_TO_AB`
   - `RUN_SYNC_TO_YNAB`
   - Akahu tokens
   - Actual Budget settings, if enabled
   - YNAB settings, if enabled
   - Sure Finance settings, if enabled

6. Start the app and check the app log. It should print the options file,
   mapping file, and sync interval before the first sync starts.

## HAOS options

The add-on reads settings from the Supervisor options UI. By default it expects
the mapping file at:

```text
/config/akahu_budget_mapping.json
```

The default `sync_interval` is `86400`, which means one sync per day. Set
`log_file` to an empty string to use Supervisor logs only; that is the default
for the add-on.

`mapping_json` is optional and intended for automation or copy/paste setup. It
is the raw contents of `akahu_budget_mapping.json`. The add-on uses it only when
`mapping_file` does not already exist.

The add-on fails loudly if the mapping file is missing or if required options
for the enabled sync target are blank.

Sure Finance sidecar mode requires access to the Sure Rails container runtime.
For a normal Home Assistant OS add-on, prefer `SURE_USE_SIDECAR: false` unless
the add-on can actually execute Docker/Podman against your Sure host.

## Updating on HAOS

Pushes to `main` publish a fresh `ghcr.io/corrin/akahu_to_budget-haos:latest`
image. Tagged releases also publish versioned images. After pulling repo
updates in the Add-on Store, update/restart the add-on so HAOS pulls the new
image.

# Running Tests

There are some tests to validate the API is still working.  You can probably ignore them.

# OpenAI

I set up the OpenAI key for mapping accounts more out of self-amusement.  I have also toyed with the idea of using it to clean payees, assign transactions to categories, etc.
For now it's not really doing anything.


# Sure Finance Sync & Deduplication Sidecar

This script natively supports pushing Akahu transactions to a self-hosted instance of [Sure Finance](https://github.com/we-promise/sure). However, there is currently an architectural quirk in the Sure Finance API that requires a temporary workaround for deduplication.

### The Problem
When syncing bank transactions, this script uses a 7-day lookback window to ensure pending transactions aren't missed when they finally settle.

While Actual Budget and YNAB natively handle this overlapping window by deduplicating payloads via an `imported_id`, the Sure Finance `POST /api/v1/transactions` endpoint currently drops `external_id` from incoming payloads due to strong parameters. This causes the Sure API to blindly duplicate transactions on every daily sync.

### The Solution (The Docker Sidecar)
To achieve native deduplication, this integration bypasses the HTTP API and pipes a self-contained Ruby script directly into the Sure Finance Rails container. This allows the script to utilize Rails' internal `find_or_initialize_by(external_id:)` logic, guaranteeing perfect database-level deduplication.

### Configuration
By default, the script looks for the `docker` or `podman` executable and attempts to execute against a container named `sure-core`.

If you are running this sync script via `systemd` or `cron` (where the `$PATH` is restricted), or if your container is named differently, set these variables in your `.env` file:
```env
SURE_CONTAINER_RUNTIME=/path/to/your/docker  # e.g., /usr/bin/docker
SURE_CONTAINER_NAME=sure-core                # The name of your Rails container
SURE_USE_SIDECAR=true                        # Set to false to revert to the HTTP API - useful once external_id accessible via API
```

### The Future: Removing the Sidecar
There is an active plan to submit a PR to the upstream Sure Finance repository to whitelist `:external_id` in their API controllers.

Once Sure Finance updates their API to accept `external_id` natively:
1. Users can simply set `SURE_USE_SIDECAR=false` in their `.env` file.
2. The Python script will instantly stop using Docker and revert to standard, idempotent HTTP `POST` requests.
3. The sidecar logic (`_push_via_sidecar`) can be safely deleted from `sure_client.py`.
