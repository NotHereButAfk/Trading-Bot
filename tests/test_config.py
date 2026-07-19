"""Config loading, the automatic paper/live mode rule, and credentials."""

import os
import stat

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


def test_validate_live_still_needs_keys():
    # Defensive sanity check: a live config with no key is rejected.
    cfg = load_config("config.example.yaml")
    cfg["trading"]["paper_trading"] = False
    cfg["exchange"]["api_key"] = ""
    with pytest.raises(ValueError, match="api_key"):
        validate_config(cfg)


def test_env_vars_override_keys(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HTX_API_KEY", "env-key")
    monkeypatch.setenv("HTX_API_SECRET", "env-secret")
    cfg = load_config("config.yaml")
    assert cfg["exchange"]["api_key"] == "env-key"
    assert cfg["exchange"]["api_secret"] == "env-secret"


# ---- the rule: no key = paper, key present = real money ----

def test_no_api_key_is_paper(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = load_config("config.yaml")  # nothing configured
    assert cfg["trading"]["paper_trading"] is True


def test_api_key_present_goes_live(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HTX_API_KEY", "k")
    monkeypatch.setenv("HTX_API_SECRET", "s")
    cfg = load_config("config.yaml")
    assert cfg["trading"]["paper_trading"] is False  # real money


def test_key_present_but_only_partial_stays_paper(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HTX_API_KEY", "k")  # secret missing
    cfg = load_config("config.yaml")
    assert cfg["trading"]["paper_trading"] is True


def test_force_paper_keeps_paper_with_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    save_credentials(str(tmp_path / "credentials.json"),
                     {"api_key": "k", "api_secret": "s", "force_paper": True})
    cfg = load_config("config.yaml")
    assert cfg["exchange"]["api_key"] == "k"
    assert cfg["trading"]["paper_trading"] is True  # practice mode wins


def test_explicit_paper_trading_true_is_honored(monkeypatch, tmp_path):
    """Back-compat: an explicit paper_trading: true in config keeps paper even
    when a key is present (only ever errs toward safety)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "trading:\n  paper_trading: true\n"
        "exchange:\n  api_key: k\n  api_secret: s\n"
    )
    cfg = load_config("config.yaml")
    assert cfg["trading"]["paper_trading"] is True


def test_going_live_via_credentials_file(monkeypatch, tmp_path):
    """The go-live flow the Settings 'Save' button performs: just a key."""
    monkeypatch.chdir(tmp_path)
    save_credentials(str(tmp_path / "credentials.json"),
                     {"api_key": "k", "api_secret": "s"})
    cfg = load_config("config.yaml")
    assert cfg["trading"]["paper_trading"] is False  # real money, no extra flag
    assert cfg["exchange"]["api_key"] == "k"


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
    cfg = load_config("config.yaml")
    assert cfg["exchange"]["api_key"] == "from_env"          # env wins
    assert cfg["exchange"]["api_secret"] == "creds_secret"   # falls back to creds


def test_credentials_file_is_owner_only(tmp_path):
    path = str(tmp_path / "credentials.json")
    save_credentials(path, {"api_key": "abc"})
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0  # no group/other perms
