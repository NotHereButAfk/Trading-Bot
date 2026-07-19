"""Configuration loading and validation."""

import copy
import os

import yaml

DEFAULTS = {
    "exchange": {
        "api_key": "",
        "api_secret": "",
        "testnet": False,
    },
    "trading": {
        "symbols": ["BTC/USDT:USDT", "ETH/USDT:USDT"],
        "timeframe": "15m",
        "leverage": 5,
        "margin_mode": "isolated",
        "paper_trading": True,
        "paper_starting_balance": 10000.0,
        "confirm_signals": True,
        "signal_expiry_minutes": 10.0,
        "poll_interval_sec": 15,
        "candle_history": 300,
        "max_open_positions": 3,
        "cooldown_minutes": 30,
    },
    "risk": {
        "risk_per_trade_pct": 1.0,
        "atr_stop_multiplier": 2.0,
        "take_profit_rr": 2.0,
        "trailing_stop": True,
        "trailing_atr_multiplier": 2.5,
        "breakeven_at_rr": 1.0,
        "max_notional_pct_of_equity": 95.0,
        "max_daily_loss_pct": 5.0,
    },
    "strategy": {
        "entry_score": 3.0,
        "exit_score": 1.5,
        "adx_min": 20.0,
        "volume_filter": True,
        "volume_factor": 1.1,
        "weights": {
            "ema": 1.0,
            "macd": 1.0,
            "rsi": 1.0,
            "bollinger": 1.0,
            "stochastic": 1.0,
        },
        "indicators": {
            "ema_fast": 9,
            "ema_slow": 21,
            "rsi_period": 14,
            "rsi_overbought": 70.0,
            "rsi_oversold": 30.0,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "bb_period": 20,
            "bb_std": 2.0,
            "stoch_k": 14,
            "stoch_d": 3,
            "stoch_smooth": 3,
            "atr_period": 14,
            "adx_period": 14,
            "volume_ma": 20,
        },
    },
    "email": {
        "enabled": False,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_password": "",
        "from_addr": "",
        "to_addrs": [],
    },
    "gui": {
        "enabled": True,
        "refresh_ms": 2000,
    },
    "logging": {
        "level": "INFO",
        "trade_log_csv": "trades.csv",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str) -> dict:
    """Load YAML config, merge over defaults, resolve env-var credentials."""
    user_cfg = {}
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            user_cfg = yaml.safe_load(fh) or {}
    cfg = _deep_merge(DEFAULTS, user_cfg)

    # Environment variables win over the file so secrets can stay out of it.
    env_map = {
        ("exchange", "api_key"): "HTX_API_KEY",
        ("exchange", "api_secret"): "HTX_API_SECRET",
        ("email", "smtp_user"): "BOT_SMTP_USER",
        ("email", "smtp_password"): "BOT_SMTP_PASSWORD",
    }
    for (section, key), env_name in env_map.items():
        if os.environ.get(env_name):
            cfg[section][key] = os.environ[env_name]

    validate_config(cfg)
    return cfg


def validate_config(cfg: dict) -> None:
    trading = cfg["trading"]
    if not trading["symbols"]:
        raise ValueError("trading.symbols must contain at least one symbol")
    if trading["leverage"] < 1 or trading["leverage"] > 125:
        raise ValueError("trading.leverage must be between 1 and 125")
    if trading["signal_expiry_minutes"] <= 0:
        raise ValueError("trading.signal_expiry_minutes must be positive")
    if not trading["paper_trading"]:
        if not cfg["exchange"]["api_key"] or not cfg["exchange"]["api_secret"]:
            raise ValueError(
                "Live trading requires exchange.api_key and exchange.api_secret "
                "(or HTX_API_KEY / HTX_API_SECRET env vars)"
            )
    risk = cfg["risk"]
    if not 0 < risk["risk_per_trade_pct"] <= 10:
        raise ValueError("risk.risk_per_trade_pct must be in (0, 10]")
    if cfg["email"]["enabled"]:
        email = cfg["email"]
        if not email["smtp_user"] or not email["smtp_password"] or not email["to_addrs"]:
            raise ValueError(
                "email.enabled requires smtp_user, smtp_password and to_addrs"
            )
