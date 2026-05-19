"""Module for handling configuration and environment variables."""

import os
import logging
from dotenv import load_dotenv

# Load environment variables with override=True to ensure .env values are used
load_dotenv(verbose=True, override=True)

for flag in ("RUN_SYNC_TO_YNAB", "RUN_SYNC_TO_AB", "RUN_SYNC_TO_SURE"):
    if os.getenv(flag) is None:
        # Default SURE to false if not present to avoid breaking existing setups
        if flag == "RUN_SYNC_TO_SURE":
            os.environ["RUN_SYNC_TO_SURE"] = "false"
        else:
            raise EnvironmentError(f"Missing required environment variable: {flag}")

RUN_SYNC_TO_YNAB = os.getenv("RUN_SYNC_TO_YNAB").lower() == "true"
RUN_SYNC_TO_AB = os.getenv("RUN_SYNC_TO_AB").lower() == "true"
RUN_SYNC_TO_SURE = os.getenv("RUN_SYNC_TO_SURE", "false").lower() == "true"
FORCE_REFRESH = os.getenv("FORCE_REFRESH", "false").lower() == "true"
DEBUG_SYNC = os.getenv("DEBUG_SYNC", "false").lower() == "true"

# actualpy is an optional dependency. Fail fast at startup if the user has
# enabled the Actual Budget sync target but did not install it.
if RUN_SYNC_TO_AB:
    try:
        import actual  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "RUN_SYNC_TO_AB=true but actualpy is not installed. "
            "Install it with: pip install -r requirements_actual.txt"
        ) from e

if not RUN_SYNC_TO_YNAB and not RUN_SYNC_TO_AB and not RUN_SYNC_TO_SURE:
    msg = (
        "At least one of RUN_SYNC_TO_YNAB, RUN_SYNC_TO_AB, "
        "RUN_SYNC_TO_SURE must be True."
    )
    logging.error(msg)
    raise EnvironmentError(msg)

required_envs = ["AKAHU_USER_TOKEN", "AKAHU_APP_TOKEN"]
if RUN_SYNC_TO_AB:
    required_envs += [
        "ACTUAL_SERVER_URL",
        "ACTUAL_PASSWORD",
        "ACTUAL_ENCRYPTION_KEY",
        "ACTUAL_SYNC_ID",
    ]
if RUN_SYNC_TO_YNAB:
    required_envs += ["YNAB_BEARER_TOKEN"]
if RUN_SYNC_TO_SURE:
    required_envs += ["SURE_API_TOKEN"]

ENVs = {key: os.getenv(key) for key in required_envs}

for key, value in ENVs.items():
    if value is None:
        raise EnvironmentError(f"Missing required environment variable: {key}")

AKAHU_ENDPOINT = "https://api.akahu.io/v1"
AKAHU_HEADERS = {
    "Authorization": f"Bearer {ENVs['AKAHU_USER_TOKEN']}",
    "X-Akahu-ID": ENVs["AKAHU_APP_TOKEN"],
}

YNAB_ENDPOINT = "https://api.ynab.com/v1/"
YNAB_HEADERS = (
    {"Authorization": f"Bearer {ENVs['YNAB_BEARER_TOKEN']}"}
    if RUN_SYNC_TO_YNAB
    else None
)
