import json

import pytest

from haos_mapping_bootstrap import (
    MAPPING_UPLOAD_OPTION,
    write_mapping_from_options,
)


def _mapping():
    return {
        "akahu_accounts": {},
        "actual_accounts": {},
        "ynab_accounts": {},
        "mapping": {},
    }


def _mapping_json(data=None):
    return json.dumps(data or _mapping())


def _write_options(path, mapping_file, **extra):
    payload = {
        "mapping_file": str(mapping_file),
        **extra,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_no_upload_option_is_noop(tmp_path):
    options_file = tmp_path / "options.json"
    mapping_file = tmp_path / "akahu_budget_mapping.json"
    _write_options(options_file, mapping_file)

    assert write_mapping_from_options(options_file=options_file) is False
    assert not mapping_file.exists()


def test_valid_upload_writes_mapping_file(tmp_path):
    options_file = tmp_path / "options.json"
    mapping_file = tmp_path / "akahu_budget_mapping.json"
    _write_options(
        options_file,
        mapping_file,
        **{MAPPING_UPLOAD_OPTION: _mapping_json()},
    )

    assert write_mapping_from_options(options_file=options_file) is True
    assert json.loads(mapping_file.read_text(encoding="utf-8")) == _mapping()
    assert mapping_file.stat().st_mode & 0o777 == 0o600


def test_existing_mapping_file_is_not_overwritten(tmp_path):
    options_file = tmp_path / "options.json"
    mapping_file = tmp_path / "akahu_budget_mapping.json"
    existing = {"existing": True}
    mapping_file.write_text(json.dumps(existing), encoding="utf-8")
    _write_options(
        options_file,
        mapping_file,
        **{MAPPING_UPLOAD_OPTION: _mapping_json()},
    )

    assert write_mapping_from_options(options_file=options_file) is False
    assert json.loads(mapping_file.read_text(encoding="utf-8")) == existing


def test_invalid_json_fails(tmp_path):
    options_file = tmp_path / "options.json"
    mapping_file = tmp_path / "akahu_budget_mapping.json"
    _write_options(options_file, mapping_file, **{MAPPING_UPLOAD_OPTION: "{not-json"})

    with pytest.raises(ValueError, match="valid JSON"):
        write_mapping_from_options(options_file=options_file)


def test_missing_required_mapping_key_fails(tmp_path):
    options_file = tmp_path / "options.json"
    mapping_file = tmp_path / "akahu_budget_mapping.json"
    bad_mapping = _mapping()
    del bad_mapping["mapping"]
    _write_options(
        options_file,
        mapping_file,
        **{MAPPING_UPLOAD_OPTION: _mapping_json(bad_mapping)},
    )

    with pytest.raises(ValueError, match="missing required keys: mapping"):
        write_mapping_from_options(options_file=options_file)
