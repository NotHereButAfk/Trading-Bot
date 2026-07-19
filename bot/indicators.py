"""Technical indicators implemented on pandas DataFrames.

All functions take a DataFrame with columns: open, high, low, close, volume
and return Series/DataFrames aligned to the input index.
"""

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.fillna(50.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0):
    middle = sma(close, period)
    std = close.rolling(period).std(ddof=0)
    upper = middle + num_std * std
    lower = middle - num_std * std
    return upper, middle, lower


def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3, smooth: int = 3):
    lowest = df["low"].rolling(k_period).min()
    highest = df["high"].rolling(k_period).max()
    span = (highest - lowest).replace(0.0, np.nan)
    raw_k = 100.0 * (df["close"] - lowest) / span
    k = raw_k.rolling(smooth).mean()
    d = k.rolling(d_period).mean()
    return k.fillna(50.0), d.fillna(50.0)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1.0 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index
    )
    tr_smooth = true_range(df).ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / tr_smooth
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / tr_smooth
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return dx.ewm(alpha=1.0 / period, adjust=False).mean().fillna(0.0)


def compute_all(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Attach every indicator the strategy needs as columns on a copy of df."""
    out = df.copy()
    out["ema_fast"] = ema(out["close"], params["ema_fast"])
    out["ema_slow"] = ema(out["close"], params["ema_slow"])
    out["rsi"] = rsi(out["close"], params["rsi_period"])
    macd_line, macd_sig, macd_hist = macd(
        out["close"], params["macd_fast"], params["macd_slow"], params["macd_signal"]
    )
    out["macd"] = macd_line
    out["macd_signal"] = macd_sig
    out["macd_hist"] = macd_hist
    bb_up, bb_mid, bb_low = bollinger(out["close"], params["bb_period"], params["bb_std"])
    out["bb_upper"] = bb_up
    out["bb_middle"] = bb_mid
    out["bb_lower"] = bb_low
    stoch_k, stoch_d = stochastic(
        out, params["stoch_k"], params["stoch_d"], params["stoch_smooth"]
    )
    out["stoch_k"] = stoch_k
    out["stoch_d"] = stoch_d
    out["atr"] = atr(out, params["atr_period"])
    out["adx"] = adx(out, params["adx_period"])
    out["volume_ma"] = sma(out["volume"], params["volume_ma"])
    return out
