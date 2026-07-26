"""Durable paper-account balance and open-position storage."""

import json
import os
import threading
from dataclasses import asdict

from .state import Trade


class PaperAccountStore:
    """Persist paper cash and open trades in a small local JSON file."""

    VERSION = 1

    def __init__(self, path: str, starting_balance: float):
        self.path = path
        self.starting_balance = float(starting_balance)
        self._lock = threading.RLock()

    def load(self) -> tuple[float, list[Trade], bool]:
        """Return (cash balance, open trades, loaded_existing_state)."""
        if not self.path or not os.path.exists(self.path):
            return self.starting_balance, [], False
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            balance = float(data["balance"])
            if balance < 0:
                raise ValueError("paper balance cannot be negative")
            trades = [Trade(**item) for item in data.get("open_trades", [])]
            return balance, trades, True
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
            return self.starting_balance, [], False

    def save(self, balance: float, open_trades) -> None:
        """Atomically save cash and open positions."""
        if not self.path:
            return
        with self._lock:
            parent = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(parent, exist_ok=True)
            temp_path = self.path + ".tmp"
            payload = {
                "version": self.VERSION,
                "balance": float(balance),
                "open_trades": [asdict(trade) for trade in open_trades],
            }
            with open(temp_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_path, self.path)

    def reset(self, amount: float) -> None:
        self.save(float(amount), [])
