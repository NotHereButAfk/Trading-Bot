"""Multi-indicator confluence strategy.

Each indicator casts a weighted vote (+ for long, - for short). A position is
opened only when the combined score clears `entry_score` AND the ADX trend
filter and volume filter agree. An open position is closed early when the
score swings past `exit_score` in the opposite direction.
"""

from dataclasses import dataclass, field

import pandas as pd

from . import indicators


@dataclass
class Signal:
    direction: str          # "long", "short" or "none"
    score: float
    price: float
    atr: float
    adx: float
    votes: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)

    @property
    def is_entry(self) -> bool:
        return self.direction in ("long", "short")


class MultiIndicatorStrategy:
    def __init__(self, cfg: dict):
        self.cfg = cfg["strategy"]
        self.params = self.cfg["indicators"]
        self.weights = self.cfg["weights"]

    def evaluate(self, df: pd.DataFrame) -> Signal:
        """Evaluate the latest *closed* candle and return an entry signal."""
        data = indicators.compute_all(df, self.params)
        row = data.iloc[-1]
        prev = data.iloc[-2]

        votes = {}
        reasons = []

        # 1. EMA trend: fast above slow is bullish.
        if row["ema_fast"] > row["ema_slow"]:
            votes["ema"] = self.weights["ema"]
            reasons.append(f"EMA{self.params['ema_fast']}>EMA{self.params['ema_slow']}")
        else:
            votes["ema"] = -self.weights["ema"]
            reasons.append(f"EMA{self.params['ema_fast']}<EMA{self.params['ema_slow']}")

        # 2. MACD histogram momentum, weighted up when it is also expanding.
        if row["macd_hist"] > 0:
            factor = 1.0 if row["macd_hist"] > prev["macd_hist"] else 0.5
            votes["macd"] = self.weights["macd"] * factor
            reasons.append("MACD hist positive")
        elif row["macd_hist"] < 0:
            factor = 1.0 if row["macd_hist"] < prev["macd_hist"] else 0.5
            votes["macd"] = -self.weights["macd"] * factor
            reasons.append("MACD hist negative")
        else:
            votes["macd"] = 0.0

        # 3. RSI momentum, neutralized in overbought/oversold extremes so we
        #    do not chase exhausted moves.
        rsi_val = row["rsi"]
        if 50.0 < rsi_val < self.params["rsi_overbought"]:
            votes["rsi"] = self.weights["rsi"]
            reasons.append(f"RSI {rsi_val:.1f} bullish")
        elif self.params["rsi_oversold"] < rsi_val < 50.0:
            votes["rsi"] = -self.weights["rsi"]
            reasons.append(f"RSI {rsi_val:.1f} bearish")
        else:
            votes["rsi"] = 0.0
            reasons.append(f"RSI {rsi_val:.1f} extreme/neutral")

        # 4. Bollinger: close relative to the middle band, but fade the vote
        #    if price has already pierced the outer band (overextended).
        if row["close"] > row["bb_middle"] and row["close"] < row["bb_upper"]:
            votes["bollinger"] = self.weights["bollinger"]
            reasons.append("Price above BB middle")
        elif row["close"] < row["bb_middle"] and row["close"] > row["bb_lower"]:
            votes["bollinger"] = -self.weights["bollinger"]
            reasons.append("Price below BB middle")
        else:
            votes["bollinger"] = 0.0
            reasons.append("Price at BB extreme")

        # 5. Stochastic %K/%D cross, ignored inside its own extremes.
        if row["stoch_k"] > row["stoch_d"] and row["stoch_k"] < 80.0:
            votes["stochastic"] = self.weights["stochastic"]
            reasons.append("Stoch %K>%D")
        elif row["stoch_k"] < row["stoch_d"] and row["stoch_k"] > 20.0:
            votes["stochastic"] = -self.weights["stochastic"]
            reasons.append("Stoch %K<%D")
        else:
            votes["stochastic"] = 0.0

        score = sum(votes.values())
        direction = "none"

        adx_ok = row["adx"] >= self.cfg["adx_min"]
        if not adx_ok:
            reasons.append(f"ADX {row['adx']:.1f} < {self.cfg['adx_min']} (no trend)")

        volume_ok = True
        if self.cfg["volume_filter"] and pd.notna(row["volume_ma"]):
            volume_ok = row["volume"] >= self.cfg["volume_factor"] * row["volume_ma"]
            if not volume_ok:
                reasons.append("Volume below threshold")

        if adx_ok and volume_ok:
            if score >= self.cfg["entry_score"]:
                direction = "long"
            elif score <= -self.cfg["entry_score"]:
                direction = "short"

        return Signal(
            direction=direction,
            score=round(score, 2),
            price=float(row["close"]),
            atr=float(row["atr"]),
            adx=float(row["adx"]),
            votes=votes,
            reasons=reasons,
        )

    def should_exit(self, df: pd.DataFrame, side: str) -> tuple[bool, str]:
        """Return (True, reason) when confluence flips against an open trade."""
        signal = self.evaluate(df)
        threshold = self.cfg["exit_score"]
        if side == "long" and signal.score <= -threshold:
            return True, f"signal flipped bearish (score {signal.score})"
        if side == "short" and signal.score >= threshold:
            return True, f"signal flipped bullish (score {signal.score})"
        return False, ""
