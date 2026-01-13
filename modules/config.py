"""Module for handling configuration and environment variables."""

import os
import logging
from dotenv import load_dotenv

# Load environment variables with override=True to ensure .env values are used
load_dotenv(verbose=True, override=True)

# Debug logging for environment variables

required_envs = [
    "ACTUAL_SERVER_URL",
    "ACTUAL_PASSWORD",
    "ACTUAL_ENCRYPTION_KEY",
    "ACTUAL_SYNC_ID",
    "AKAHU_USER_TOKEN",
    "AKAHU_APP_TOKEN",
    "YNAB_BEARER_TOKEN",
    "RUN_SYNC_TO_YNAB",
    "RUN_SYNC_TO_AB",
]

# Load environment variables into a dictionary for validation
ENVs = {key: os.getenv(key) for key in required_envs}

# Validate environment variables
for key, value in ENVs.items():
    if value is None:
        raise EnvironmentError(f"Missing required environment variable: {key}")

# API endpoints and headers
YNAB_ENDPOINT = "https://api.ynab.com/v1/"
YNAB_HEADERS = {"Authorization": f"Bearer {ENVs['YNAB_BEARER_TOKEN']}"}

AKAHU_ENDPOINT = "https://api.akahu.io/v1"
AKAHU_HEADERS = {
    "Authorization": f"Bearer {ENVs['AKAHU_USER_TOKEN']}",
    "X-Akahu-ID": ENVs["AKAHU_APP_TOKEN"],
}

# Load boolean flags from environment variables with defaults
RUN_SYNC_TO_YNAB = ENVs["RUN_SYNC_TO_YNAB"].lower() == "true"
RUN_SYNC_TO_AB = ENVs["RUN_SYNC_TO_AB"].lower() == "true"
FORCE_REFRESH = os.getenv("FORCE_REFRESH", "false").lower() == "true"
DEBUG_SYNC = os.getenv("DEBUG_SYNC", "false").lower() == "true"

# Validate that at least one sync target is enabled
if not RUN_SYNC_TO_YNAB and not RUN_SYNC_TO_AB:
    logging.error(
        "Environment variable RUN_SYNC_TO_YNAB or RUN_SYNC_TO_AB must be True."
    )
    raise EnvironmentError(
        "Environment variable RUN_SYNC_TO_YNAB or RUN_SYNC_TO_AB must be True."
    )
