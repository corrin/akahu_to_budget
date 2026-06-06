"""Configuration loading for all deployment targets.

The public module constants are kept for existing call sites. Underneath, the
values come from a typed config loader that can read normal env/.env config or
Home Assistant add-on options from JSON.
"""

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv


HA_OPTIONS_FILE_ENV = "AKAHU_TO_BUDGET_OPTIONS_FILE"
DEFAULT_HA_OPTIONS_FILE = "/data/options.json"
DEFAULT_MAPPING_FILE = "akahu_budget_mapping.json"
DEFAULT_LOG_FILE = "app.log"
DEFAULT_SYNC_INTERVAL = 86400


@dataclass(frozen=True)
class AppConfig:
    run_sync_to_ynab: bool
    run_sync_to_ab: bool
    run_sync_to_sure: bool
    force_refresh: bool
    debug_sync: bool
    mapping_file: str
    log_file: str | None
    sync_interval: int
    envs: dict[str, str]
    akahu_endpoint: str = "https://api.akahu.io/v1"
    ynab_endpoint: str = "https://api.ynab.com/v1/"

    @property
    def akahu_headers(self):
        return {
            "Authorization": f"Bearer {self.envs['AKAHU_USER_TOKEN']}",
            "X-Akahu-ID": self.envs["AKAHU_APP_TOKEN"],
        }

    @property
    def ynab_headers(self):
        if not self.run_sync_to_ynab:
            return None
        return {"Authorization": f"Bearer {self.envs['YNAB_BEARER_TOKEN']}"}


def _bool_value(value, key):
    if isinstance(value, bool):
        return value
    if value is None:
        raise EnvironmentError(f"Missing required environment variable: {key}")
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise EnvironmentError(f"Invalid boolean value for {key}: {value}")


def _optional_str(value):
    if value is None:
        return None
    value = str(value).strip()
    if value == "":
        return None
    return value


def _read_options_file(options_file, *, required):
    path = Path(options_file)
    if not path.exists():
        if required:
            raise EnvironmentError(f"Options file not found: {path}")
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise EnvironmentError(f"Invalid JSON in options file {path}: {e}") from e
    if not isinstance(data, dict):
        raise EnvironmentError(f"Options file {path} must contain a JSON object")
    return data


def _config_sources(options_file=None):
    load_dotenv(verbose=True, override=True)

    values = dict(os.environ)
    if options_file is not None:
        selected_options_file = options_file
        options_file_required = True
    else:
        selected_options_file = os.getenv(HA_OPTIONS_FILE_ENV)
        options_file_required = selected_options_file is not None

    if selected_options_file is None and Path(DEFAULT_HA_OPTIONS_FILE).exists():
        selected_options_file = DEFAULT_HA_OPTIONS_FILE
        options_file_required = False

    if selected_options_file is None:
        return values

    values.update(
        _read_options_file(selected_options_file, required=options_file_required)
    )
    return values


def load_config(overrides=None, options_file=None):
    """Load validated config from env/.env, HA options, and explicit overrides."""
    values = _config_sources(options_file=options_file)
    if overrides is None:
        overrides = {}
    values.update({k: v for k, v in overrides.items() if v is not None})

    run_sync_to_ynab = _bool_value(values.get("RUN_SYNC_TO_YNAB"), "RUN_SYNC_TO_YNAB")
    run_sync_to_ab = _bool_value(values.get("RUN_SYNC_TO_AB"), "RUN_SYNC_TO_AB")
    run_sync_to_sure = _bool_value(
        values.get("RUN_SYNC_TO_SURE", False), "RUN_SYNC_TO_SURE"
    )
    force_refresh = _bool_value(values.get("FORCE_REFRESH", False), "FORCE_REFRESH")
    debug_sync = _bool_value(values.get("DEBUG_SYNC", False), "DEBUG_SYNC")

    if run_sync_to_ab:
        try:
            import actual  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "RUN_SYNC_TO_AB=true but actualpy is not installed. "
                "Install it with: pip install -r requirements_actual.txt"
            ) from e

    if not run_sync_to_ynab and not run_sync_to_ab and not run_sync_to_sure:
        msg = (
            "At least one of RUN_SYNC_TO_YNAB, RUN_SYNC_TO_AB, "
            "RUN_SYNC_TO_SURE must be True."
        )
        logging.error(msg)
        raise EnvironmentError(msg)

    required_envs = ["AKAHU_USER_TOKEN", "AKAHU_APP_TOKEN"]
    if run_sync_to_ab:
        required_envs += [
            "ACTUAL_SERVER_URL",
            "ACTUAL_PASSWORD",
            "ACTUAL_ENCRYPTION_KEY",
            "ACTUAL_SYNC_ID",
        ]
    if run_sync_to_ynab:
        required_envs += ["YNAB_BEARER_TOKEN"]
    if run_sync_to_sure:
        required_envs += ["SURE_API_TOKEN"]

    envs = {key: _optional_str(values.get(key)) for key in required_envs}
    optional_env_keys = [
        "YNAB_BUDGET_ID",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "SURE_API_URL",
        "SURE_CONTAINER_RUNTIME",
        "SURE_CONTAINER_NAME",
        "SURE_USE_SIDECAR",
    ]
    optional_envs = {
        key: _optional_str(values.get(key))
        for key in optional_env_keys
    }
    envs.update(
        {key: value for key, value in optional_envs.items() if value is not None}
    )

    for key, value in envs.items():
        if key in required_envs and value is None:
            raise EnvironmentError(f"Missing required environment variable: {key}")

    sync_interval_value = values.get(
        "SYNC_INTERVAL",
        values.get("sync_interval", DEFAULT_SYNC_INTERVAL),
    )
    sync_interval = int(sync_interval_value)
    if sync_interval <= 0:
        raise EnvironmentError("SYNC_INTERVAL must be greater than zero")

    mapping_file = _optional_str(values.get("MAPPING_FILE", values.get("mapping_file")))
    log_file = _optional_str(
        values.get("LOG_FILE", values.get("log_file", DEFAULT_LOG_FILE))
    )
    if mapping_file is None:
        mapping_file = DEFAULT_MAPPING_FILE

    return AppConfig(
        run_sync_to_ynab=run_sync_to_ynab,
        run_sync_to_ab=run_sync_to_ab,
        run_sync_to_sure=run_sync_to_sure,
        force_refresh=force_refresh,
        debug_sync=debug_sync,
        mapping_file=mapping_file,
        log_file=log_file,
        sync_interval=sync_interval,
        envs=envs,
    )


CONFIG = load_config()

RUN_SYNC_TO_YNAB = CONFIG.run_sync_to_ynab
RUN_SYNC_TO_AB = CONFIG.run_sync_to_ab
RUN_SYNC_TO_SURE = CONFIG.run_sync_to_sure
FORCE_REFRESH = CONFIG.force_refresh
DEBUG_SYNC = CONFIG.debug_sync
MAPPING_FILE = CONFIG.mapping_file
LOG_FILE = CONFIG.log_file
SYNC_INTERVAL = CONFIG.sync_interval
ENVs = CONFIG.envs

AKAHU_ENDPOINT = CONFIG.akahu_endpoint
AKAHU_HEADERS = CONFIG.akahu_headers

YNAB_ENDPOINT = CONFIG.ynab_endpoint
YNAB_HEADERS = CONFIG.ynab_headers
