"""Prepare the HAOS mapping file from Supervisor options when requested."""

from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULT_MAPPING_FILE = "/config/akahu_budget_mapping.json"
DEFAULT_OPTIONS_FILE = "/data/options.json"
MAPPING_UPLOAD_OPTION = "mapping_json"
REQUIRED_MAPPING_KEYS = {
    "akahu_accounts",
    "actual_accounts",
    "ynab_accounts",
    "mapping",
}


def _load_options(options_file: str | os.PathLike[str]) -> dict:
    path = Path(options_file)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Options file {path} must contain a JSON object")
    return data


def _decode_mapping(upload_value: str) -> dict:
    try:
        data = json.loads(upload_value)
    except json.JSONDecodeError as e:
        raise ValueError(f"{MAPPING_UPLOAD_OPTION} does not contain valid JSON: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("Uploaded mapping must contain a JSON object")

    missing_keys = REQUIRED_MAPPING_KEYS - data.keys()
    if missing_keys:
        missing = ", ".join(sorted(missing_keys))
        raise ValueError(f"Uploaded mapping is missing required keys: {missing}")

    return data


def write_mapping_from_options(
    *,
    options_file: str | os.PathLike[str] = DEFAULT_OPTIONS_FILE,
) -> bool:
    """Write the mapping file from HAOS options only when the file is missing."""
    options = _load_options(options_file)
    mapping_file = Path(options.get("mapping_file") or DEFAULT_MAPPING_FILE)
    upload_value = str(options.get(MAPPING_UPLOAD_OPTION) or "").strip()

    if mapping_file.exists():
        print(f"Mapping file already exists: {mapping_file}")
        return False

    if not upload_value:
        print(f"No {MAPPING_UPLOAD_OPTION} option provided.")
        return False

    mapping_data = _decode_mapping(upload_value)
    mapping_file.parent.mkdir(parents=True, exist_ok=True)
    with mapping_file.open("w", encoding="utf-8") as f:
        json.dump(mapping_data, f, indent=4)
        f.write("\n")
    mapping_file.chmod(0o600)
    print(f"Mapping file created from Home Assistant options: {mapping_file}")
    return True


def main() -> int:
    options_file = os.getenv("AKAHU_TO_BUDGET_OPTIONS_FILE", DEFAULT_OPTIONS_FILE)
    try:
        write_mapping_from_options(options_file=options_file)
    except Exception as e:
        print(f"ERROR: Failed to prepare mapping file: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
