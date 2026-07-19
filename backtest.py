#!/usr/bin/env python3
"""Backtest the multi-indicator strategy on historical HTX candles.

Runs the exact same signal, sizing, stop/target and trailing-stop code the
live bot uses, candle by candle, and reports win rate, PnL, drawdown and a
per-trade breakdown.

Usage:
    python backtest.py --symbol "BTC/USDT:USDT" --timeframe 15m --candles 2000
    python backtest.py --csv history.csv                 # offline data
    python backtest.py --out trades_backtest.csv         # save the trade list

CSV format: timestamp,open,high,low,close,volume  (timestamp in ms or ISO8601).
"""

import argparse
import time
from dataclasses import dataclass, field

import pandas as pd

from bot import indicators
from bot.config import load_config
from bot.risk import RiskManager
from bot.strategy import MultiIndicatorStrategy

TAKER_FEE = 0.0005

_TIMEFRAME_SEC = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


@dataclass
class BacktestTrade:
    side: str
    entry_time: object
    entry_price: float
    base_amount: float
    stop_loss: float
    take_profit: float
    initial_stop: float
    risk_amount: float
    exit_time: object = None
    exit_price: float = None
    pnl: float = None
    reason: str = None

    @property
    def r_multiple(self) -> float:
        return self.pnl / self.risk_amount if self.risk_amount else 0.0


@dataclass
class BacktestResult:
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    starting_equity: float = 0.0
    ending_equity: float = 0.0

    @property
    def wins(self):
        return [t for t in self.trades if t.pnl > 0]

    @property
    def losses(self):
        return [t for t in self.trades if t.pnl <= 0]

    @property
    def max_drawdown_pct(self) -> float:
        peak, worst = float("-inf"), 0.0
        for eq in self.equity_curve:
            peak = max(peak, eq)
            if peak > 0:
                worst = max(worst, (peak - eq) / peak * 100.0)
        return worst

    @property
    def profit_factor(self) -> float:
        gross_win = sum(t.pnl for t in self.wins)
        gross_loss = abs(sum(t.pnl for t in self.losses))
        return gross_win / gross_loss if gross_loss > 0 else float("inf")


class Backtester:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.strategy = MultiIndicatorStrategy(cfg)
        self.risk = RiskManager(cfg)
        self.params = cfg["strategy"]["indicators"]
        self.trading = cfg["trading"]

    def run(self, df: pd.DataFrame, warmup: int = 60) -> BacktestResult:
        data = indicators.compute_all(df, self.params)
        equity = float(self.trading["paper_starting_balance"])
        result = BacktestResult(starting_equity=equity)

        cooldown_candles = 0
        timeframe_sec = _TIMEFRAME_SEC.get(self.trading["timeframe"], 900)
        cooldown_len = int(self.trading["cooldown_minutes"] * 60 / timeframe_sec)
        open_trade: BacktestTrade | None = None
        day_stamp, day_start_equity, halted = None, equity, False
        max_daily_loss = self.cfg["risk"]["max_daily_loss_pct"]

        for i in range(warmup, len(data)):
            row = data.iloc[i]
            candle_day = data.index[i].date()
            if candle_day != day_stamp:
                day_stamp, day_start_equity, halted = candle_day, equity, False

            # ---- manage the open position on this candle's range ----
            if open_trade is not None:
                exited = self._check_exit(open_trade, row, data, i)
                if exited:
                    equity += open_trade.pnl
                    result.trades.append(open_trade)
                    open_trade = None
                    cooldown_candles = cooldown_len
                else:
                    new_stop = self.risk.update_trailing_stop(
                        open_trade, float(row["close"]), float(row["atr"])
                    )
                    open_trade.stop_loss = new_stop

            # ---- daily loss circuit breaker ----
            if not halted and day_start_equity > 0:
                loss_pct = (day_start_equity - equity) / day_start_equity * 100.0
                if loss_pct >= max_daily_loss:
                    halted = True

            # ---- look for an entry on the closed candle ----
            if open_trade is None:
                if cooldown_candles > 0:
                    cooldown_candles -= 1
                elif not halted:
                    signal = self.strategy.evaluate_at(data, i)
                    if signal.is_entry:
                        price = float(row["close"])
                        plan = self.risk.build_plan(
                            signal.direction, price, signal.atr, equity
                        )
                        if plan is not None and plan.base_amount > 0:
                            equity -= plan.base_amount * price * TAKER_FEE
                            open_trade = BacktestTrade(
                                side=signal.direction,
                                entry_time=data.index[i],
                                entry_price=price,
                                base_amount=plan.base_amount,
                                stop_loss=plan.stop_loss,
                                take_profit=plan.take_profit,
                                initial_stop=plan.stop_loss,
                                risk_amount=plan.risk_amount,
                            )

            mark = float(row["close"])
            unrealized = 0.0
            if open_trade is not None:
                direction = 1.0 if open_trade.side == "long" else -1.0
                unrealized = direction * (mark - open_trade.entry_price) * open_trade.base_amount
            result.equity_curve.append(equity + unrealized)

        # Close anything still open at the last candle so results are complete.
        if open_trade is not None:
            self._settle(open_trade, data.index[-1], float(data.iloc[-1]["close"]),
                         "end of data")
            equity += open_trade.pnl
            result.trades.append(open_trade)

        result.ending_equity = equity
        return result

    def _check_exit(self, trade: BacktestTrade, row, data, i) -> bool:
        """Stop / target on the candle's high-low range, then signal flip."""
        ts = data.index[i]
        low, high, close = float(row["low"]), float(row["high"]), float(row["close"])
        if trade.side == "long":
            if low <= trade.stop_loss:
                self._settle(trade, ts, trade.stop_loss, "stop loss")
                return True
            if high >= trade.take_profit:
                self._settle(trade, ts, trade.take_profit, "take profit")
                return True
        else:
            if high >= trade.stop_loss:
                self._settle(trade, ts, trade.stop_loss, "stop loss")
                return True
            if low <= trade.take_profit:
                self._settle(trade, ts, trade.take_profit, "take profit")
                return True
        flipped, why = self.strategy.should_exit_at(data, i, trade.side)
        if flipped:
            self._settle(trade, ts, close, why)
            return True
        return False

    def _settle(self, trade: BacktestTrade, ts, exit_price: float, reason: str):
        direction = 1.0 if trade.side == "long" else -1.0
        gross = direction * (exit_price - trade.entry_price) * trade.base_amount
        fee = trade.base_amount * exit_price * TAKER_FEE
        trade.exit_time = ts
        trade.exit_price = exit_price
        trade.pnl = gross - fee
        trade.reason = reason


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    ts = df["timestamp"]
    if pd.api.types.is_numeric_dtype(ts):
        df["timestamp"] = pd.to_datetime(ts, unit="ms", utc=True)
    else:
        df["timestamp"] = pd.to_datetime(ts, utc=True)
    return df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]


def print_report(symbol: str, timeframe: str, result: BacktestResult):
    trades = result.trades
    print(f"\n=== Backtest: {symbol} {timeframe} ===")
    if not trades:
        print("No trades were taken. Try more candles or looser thresholds.")
        return
    total_pnl = result.ending_equity - result.starting_equity
    win_rate = len(result.wins) / len(trades) * 100.0
    avg_win = (sum(t.pnl for t in result.wins) / len(result.wins)) if result.wins else 0.0
    avg_loss = (sum(t.pnl for t in result.losses) / len(result.losses)) if result.losses else 0.0
    print(f"Trades:         {len(trades)}  ({len(result.wins)} wins / {len(result.losses)} losses)")
    print(f"Win rate:       {win_rate:.1f}%")
    print(f"Net PnL:        {total_pnl:+.2f} USDT "
          f"({total_pnl / result.starting_equity * 100.0:+.2f}%)")
    print(f"Ending equity:  {result.ending_equity:.2f} USDT")
    print(f"Profit factor:  {result.profit_factor:.2f}")
    print(f"Avg win/loss:   {avg_win:+.2f} / {avg_loss:+.2f} USDT")
    print(f"Max drawdown:   {result.max_drawdown_pct:.2f}%")
    reasons = {}
    for t in trades:
        reasons.setdefault(t.reason, [0, 0.0])
        reasons[t.reason][0] += 1
        reasons[t.reason][1] += t.pnl
    print("Exits by reason:")
    for reason, (count, pnl) in sorted(reasons.items(), key=lambda kv: -kv[1][0]):
        print(f"  {reason:<28} {count:>4}  ({pnl:+.2f} USDT)")


def save_trades(path: str, trades: list):
    rows = [
        {
            "entry_time": t.entry_time, "exit_time": t.exit_time, "side": t.side,
            "entry_price": t.entry_price, "exit_price": t.exit_price,
            "size": t.base_amount, "pnl": round(t.pnl, 4),
            "r_multiple": round(t.r_multiple, 2), "reason": t.reason,
        }
        for t in trades
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    print(f"Trade list written to {path}")


def main():
    parser = argparse.ArgumentParser(description="Backtest the HTX bot strategy")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--symbol", default=None, help="defaults to first config symbol")
    parser.add_argument("--timeframe", default=None, help="defaults to config timeframe")
    parser.add_argument("--candles", type=int, default=2000)
    parser.add_argument("--csv", default=None, help="use a local OHLCV csv instead of HTX")
    parser.add_argument("--out", default=None, help="write per-trade results csv here")
    args = parser.parse_args()

    cfg = load_config(args.config)
    symbol = args.symbol or cfg["trading"]["symbols"][0]
    timeframe = args.timeframe or cfg["trading"]["timeframe"]
    cfg["trading"]["timeframe"] = timeframe

    if args.csv:
        df = load_csv(args.csv)
        print(f"Loaded {len(df)} candles from {args.csv}")
    else:
        from bot.exchange import HTXFutures
        print(f"Fetching {args.candles} x {timeframe} candles for {symbol} from HTX...")
        exchange = HTXFutures(cfg)
        df = exchange.fetch_ohlcv(symbol, timeframe, args.candles)
        print(f"Got {len(df)} candles ({df.index[0]} .. {df.index[-1]})")

    started = time.time()
    result = Backtester(cfg).run(df)
    print_report(symbol, timeframe, result)
    print(f"\n(ran in {time.time() - started:.1f}s)")
    if args.out:
        save_trades(args.out, result.trades)


if __name__ == "__main__":
    main()
