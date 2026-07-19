"""Config loading and the live-trading safety gates."""

import pytest

from bot.config import load_config, validate_config


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
