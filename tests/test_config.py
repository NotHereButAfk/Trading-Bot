"""Config loading and the live-trading safety gates."""

import json

import pytest

from bot.config import (
    load_config,
    load_credentials_file,
    save_credentials,
    validate_config,
)


def test_defaults_are_paper():
    cfg = load_config("/nonexistent-config.yaml")
    assert cfg["trading"]["paper_trading"] is True
    assert cfg["exchange"]["confirm_live"] is False


def test_live_requires_api_keys():
    cfg = load_config("config.example.yaml")
    cfg["trading"]["paper_trading"] = False
    cfg["exchange"]["confirm_live"] = True
    cfg["exchange"]["api_key"] = ""
    with pytest.raises(ValueError, match="api_key"):
        validate_config(cfg)


def test_live_requires_confirm_live_flag():
    cfg = load_config("config.example.yaml")
    cfg["trading"]["paper_trading"] = False
    cfg["exchange"]["api_key"] = "k"
    cfg["exchange"]["api_secret"] = "s"
    cfg["exchange"]["confirm_live"] = False
    with pytest.raises(ValueError, match="confirm_live"):
        validate_config(cfg)


def test_live_ok_with_keys_and_confirmation():
    cfg = load_config("config.example.yaml")
    cfg["trading"]["paper_trading"] = False
    cfg["exchange"]["api_key"] = "k"
    cfg["exchange"]["api_secret"] = "s"
    cfg["exchange"]["confirm_live"] = True
    validate_config(cfg)  # should not raise


def test_env_vars_override_keys(monkeypatch):
    monkeypatch.setenv("HTX_API_KEY", "env-key")
    monkeypatch.setenv("HTX_API_SECRET", "env-secret")
    cfg = load_config("config.example.yaml")
    assert cfg["exchange"]["api_key"] == "env-key"
    assert cfg["exchange"]["api_secret"] == "env-secret"


# ---- credentials file written by the in-app Settings screen ----

def test_save_and_load_credentials_roundtrip(tmp_path):
    path = str(tmp_path / "credentials.json")
    save_credentials(path, {"api_key": "abc", "api_secret": "xyz"})
    data = load_credentials_file(path)
    assert data["api_key"] == "abc"
    assert data["api_secret"] == "xyz"


def test_save_credentials_ignores_blank_and_keeps_existing(tmp_path):
    path = str(tmp_path / "credentials.json")
    save_credentials(path, {"api_key": "abc", "api_secret": "xyz"})
    # Re-save with a blank secret: the old secret must survive.
    save_credentials(path, {"api_key": "newkey", "api_secret": ""})
    data = load_credentials_file(path)
    assert data["api_key"] == "newkey"
    assert data["api_secret"] == "xyz"


def test_save_credentials_rejects_unknown_fields(tmp_path):
    path = str(tmp_path / "credentials.json")
    save_credentials(path, {"api_key": "abc", "evil": "nope"})
    data = load_credentials_file(path)
    assert "evil" not in data


def test_credentials_file_overrides_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # a config.yaml with one key, a credentials.json overriding it
    (tmp_path / "config.yaml").write_text(
        "exchange:\n  api_key: from_yaml\n  api_secret: yaml_secret\n"
    )
    save_credentials(str(tmp_path / "credentials.json"),
                     {"api_key": "from_creds", "api_secret": "creds_secret"})
    cfg = load_config("config.yaml")
    assert cfg["exchange"]["api_key"] == "from_creds"
    assert cfg["exchange"]["api_secret"] == "creds_secret"


def test_env_beats_credentials_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_credentials(str(tmp_path / "credentials.json"),
                     {"api_key": "from_creds", "api_secret": "creds_secret"})
    monkeypatch.setenv("HTX_API_KEY", "from_env")
    cfg = load_config("config.yaml")  # no config.yaml -> defaults + creds + env
    assert cfg["exchange"]["api_key"] == "from_env"          # env wins
    assert cfg["exchange"]["api_secret"] == "creds_secret"   # falls back to creds


def test_going_live_via_credentials_file(tmp_path, monkeypatch):
    """The whole go-live flow the Settings 'Save' button performs."""
    monkeypatch.chdir(tmp_path)
    save_credentials(str(tmp_path / "credentials.json"), {
        "api_key": "k", "api_secret": "s",
        "paper_trading": False, "confirm_live": True,
    })
    cfg = load_config("config.yaml")  # must validate as a live config
    assert cfg["trading"]["paper_trading"] is False
    assert cfg["exchange"]["confirm_live"] is True
    assert cfg["exchange"]["api_key"] == "k"


def test_credentials_file_is_owner_only(tmp_path):
    import os
    import stat
    path = str(tmp_path / "credentials.json")
    save_credentials(path, {"api_key": "abc"})
    mode = stat.S_IMODE(os.stat(path).st_mode)
    # No group/other permissions.
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
