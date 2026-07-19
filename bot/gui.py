"""Tkinter dashboard: open trades, PnL, trade history and the signal log.

Runs on the main thread; the trading loop runs on a daemon thread and the GUI
polls BotState.snapshot() on a timer, so no Tk calls ever cross threads.
"""

import time
import tkinter as tk
from tkinter import messagebox, ttk

from . import config as bot_config
from .state import BotState

BG = "#12141a"
PANEL = "#1b1e27"
FG = "#e6e6e6"
DIM = "#8a8f9e"
GREEN = "#2ecc71"
RED = "#e74c3c"
ACCENT = "#3498db"
AMBER = "#c9a227"


class Dashboard:
    def __init__(self, state: BotState, refresh_ms: int = 2000, on_close=None,
                 cfg: dict | None = None, test_connection=None, on_restart=None):
        self.state = state
        self.refresh_ms = refresh_ms
        self.on_close = on_close
        self.cfg = cfg or {}
        # Optional callback: (api_key, api_secret) -> (ok: bool, message: str)
        self.test_connection = test_connection
        # Optional: set when the user asks to apply settings via a clean restart.
        self.on_restart = on_restart
        self.restart_requested = False
        self._credentials_path = (
            bot_config.credentials_path(self.cfg) if self.cfg else "credentials.json"
        )

        self.root = tk.Tk()
        self.root.title("HTX Futures Bot — Control Panel")
        self.root.geometry("1040x800")
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_close)
        self._build_styles()
        self._build_layout()

    # ------------------------------------------------------------------ ui

    def _build_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background=PANEL, fieldbackground=PANEL, foreground=FG,
            rowheight=26, borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#232734", foreground=FG, borderwidth=0, relief="flat",
        )
        style.map("Treeview", background=[("selected", "#2c3e50")])

    def _build_layout(self):
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=12, pady=(12, 6))

        self.status_var = tk.StringVar(value="starting...")
        self.mode_var = tk.StringVar(value="")
        self.equity_var = tk.StringVar(value="--")
        self.pnl_var = tk.StringVar(value="--")

        tk.Label(header, textvariable=self.mode_var, bg=BG, fg=ACCENT,
                 font=("TkDefaultFont", 14, "bold")).pack(side="left")
        tk.Button(header, text="⚙ Settings / API Key",
                  command=self._open_settings,
                  bg="#232734", fg=FG, activebackground=ACCENT, activeforeground=BG,
                  relief="flat", padx=12, pady=2).pack(side="left", padx=16)
        self.pnl_label = tk.Label(header, textvariable=self.pnl_var, bg=BG, fg=FG,
                                  font=("TkDefaultFont", 13, "bold"))
        self.pnl_label.pack(side="right")
        tk.Label(header, textvariable=self.equity_var, bg=BG, fg=FG,
                 font=("TkDefaultFont", 13, "bold")).pack(side="right", padx=16)
        tk.Label(self.root, textvariable=self.status_var, bg=BG, fg=DIM, anchor="w",
                 font=("TkDefaultFont", 11)).pack(fill="x", padx=12, pady=(0, 6))

        # Pending signals panel: signals wait here for Confirm / Dismiss
        signal_header = tk.Frame(self.root, bg=BG)
        signal_header.pack(fill="x", padx=12)
        tk.Label(signal_header, text="Pending signals", bg=BG, fg=FG, anchor="w",
                 font=("TkDefaultFont", 12, "bold")).pack(side="left")
        self.dismiss_btn = tk.Button(
            signal_header, text="✗ Dismiss", command=self._dismiss_selected,
            bg="#5c2b29", fg=FG, activebackground=RED, activeforeground=FG,
            relief="flat", padx=14, pady=2, state="disabled",
        )
        self.dismiss_btn.pack(side="right", padx=(6, 0))
        self.confirm_btn = tk.Button(
            signal_header, text="✓ Confirm trade", command=self._confirm_selected,
            bg="#1e5c3a", fg=FG, activebackground=GREEN, activeforeground=BG,
            relief="flat", padx=14, pady=2, state="disabled",
        )
        self.confirm_btn.pack(side="right")

        pending_cols = ("id", "symbol", "action", "score", "price", "adx", "expires")
        self.pending_tree = ttk.Treeview(
            self.root, columns=pending_cols, show="headings", height=4
        )
        for col, label, width in (
            ("id", "ID", 60), ("symbol", "Symbol", 140), ("action", "Action", 110),
            ("score", "Score", 80), ("price", "Signal price", 120),
            ("adx", "ADX", 70), ("expires", "Expires in", 110),
        ):
            self.pending_tree.heading(col, text=label)
            self.pending_tree.column(col, width=width, anchor="center")
        self.pending_tree.tag_configure("long", foreground=GREEN)
        self.pending_tree.tag_configure("short", foreground=RED)
        self.pending_tree.bind("<<TreeviewSelect>>", self._on_signal_select)
        self.pending_tree.pack(fill="x", padx=12, pady=(4, 10))

        # Open trades table with a manual close control
        trades_header = tk.Frame(self.root, bg=BG)
        trades_header.pack(fill="x", padx=12)
        tk.Label(trades_header, text="Open trades", bg=BG, fg=FG, anchor="w",
                 font=("TkDefaultFont", 12, "bold")).pack(side="left")
        self.close_btn = tk.Button(
            trades_header, text="Close position", command=self._close_selected,
            bg="#4a4326", fg=FG, activebackground="#c9a227", activeforeground=BG,
            relief="flat", padx=14, pady=2, state="disabled",
        )
        self.close_btn.pack(side="right")

        open_cols = ("id", "symbol", "side", "size", "entry", "mark", "upnl",
                     "sl", "tp", "opened")
        self.open_tree = ttk.Treeview(self.root, columns=open_cols, show="headings", height=8)
        headings = {
            "id": ("ID", 90), "symbol": ("Symbol", 130), "side": ("Side", 70),
            "size": ("Size", 110), "entry": ("Entry", 110), "mark": ("Mark", 110),
            "upnl": ("uPnL (USDT)", 110), "sl": ("Stop", 110), "tp": ("Target", 110),
            "opened": ("Opened (UTC)", 140),
        }
        for col, (label, width) in headings.items():
            self.open_tree.heading(col, text=label)
            self.open_tree.column(col, width=width, anchor="center")
        self.open_tree.tag_configure("profit", foreground=GREEN)
        self.open_tree.tag_configure("loss", foreground=RED)
        self.open_tree.bind("<<TreeviewSelect>>", self._on_trade_select)
        self.open_tree.pack(fill="x", padx=12, pady=(4, 10))

        # Bottom: closed trades + signal log side by side
        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        left = tk.Frame(bottom, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        tk.Label(left, text="Closed trades", bg=BG, fg=FG, anchor="w",
                 font=("TkDefaultFont", 12, "bold")).pack(fill="x")
        closed_cols = ("symbol", "side", "entry", "exit", "pnl", "reason")
        self.closed_tree = ttk.Treeview(left, columns=closed_cols, show="headings")
        for col, label, width in (
            ("symbol", "Symbol", 110), ("side", "Side", 60), ("entry", "Entry", 90),
            ("exit", "Exit", 90), ("pnl", "PnL", 90), ("reason", "Reason", 150),
        ):
            self.closed_tree.heading(col, text=label)
            self.closed_tree.column(col, width=width, anchor="center")
        self.closed_tree.tag_configure("profit", foreground=GREEN)
        self.closed_tree.tag_configure("loss", foreground=RED)
        self.closed_tree.pack(fill="both", expand=True, pady=(4, 0))

        right = tk.Frame(bottom, bg=BG)
        right.pack(side="right", fill="both", expand=True, padx=(6, 0))
        tk.Label(right, text="Signal log", bg=BG, fg=FG, anchor="w",
                 font=("TkDefaultFont", 12, "bold")).pack(fill="x")
        self.log_text = tk.Text(right, bg=PANEL, fg=DIM, relief="flat",
                                state="disabled", wrap="none",
                                font=("TkFixedFont", 9))
        self.log_text.pack(fill="both", expand=True, pady=(4, 0))

    # ------------------------------------------------------------- actions

    def _selected_signal_id(self) -> str | None:
        selection = self.pending_tree.selection()
        return selection[0] if selection else None

    def _on_signal_select(self, _event=None):
        has_selection = self._selected_signal_id() is not None
        button_state = "normal" if has_selection else "disabled"
        self.confirm_btn.configure(state=button_state)
        self.dismiss_btn.configure(state=button_state)

    def _confirm_selected(self):
        signal_id = self._selected_signal_id()
        if signal_id is None:
            return
        if self.state.confirm_signal(signal_id):
            self.state.log_signal("*", f"signal {signal_id} confirmed from control panel")
        else:
            self.state.log_signal("*", f"signal {signal_id} could not be confirmed (expired?)")
        self._refresh_now()

    def _dismiss_selected(self):
        signal_id = self._selected_signal_id()
        if signal_id is None:
            return
        if self.state.dismiss_signal(signal_id):
            self.state.log_signal("*", f"signal {signal_id} dismissed from control panel")
        self._refresh_now()

    def _selected_trade_id(self) -> str | None:
        selection = self.open_tree.selection()
        return selection[0] if selection else None

    def _on_trade_select(self, _event=None):
        has_selection = self._selected_trade_id() is not None
        self.close_btn.configure(state="normal" if has_selection else "disabled")

    def _close_selected(self):
        trade_id = self._selected_trade_id()
        if trade_id is None:
            return
        if self.state.request_close(trade_id):
            self.state.log_signal("*", f"close of {trade_id} requested from control panel")
        self._refresh_now()

    # -------------------------------------------------------------- refresh

    def _refresh_now(self):
        """Immediate redraw after a button press, without stacking timers."""
        self._draw(self.state.snapshot())

    def _refresh(self):
        self._draw(self.state.snapshot())
        self.root.after(self.refresh_ms, self._refresh)

    def _draw(self, snap):
        self.mode_var.set(f"HTX Futures Bot — {snap['mode'].upper()}")
        entry_mode = snap.get("entry_mode", "auto")
        self.status_var.set(f"status: {snap['status']}   |   entries: {entry_mode}")
        self.equity_var.set(f"Equity: {snap['equity']:.2f} USDT")
        total_pnl = snap["equity"] - snap["starting_equity"]
        self.pnl_var.set(f"Session PnL: {total_pnl:+.2f} USDT")
        self.pnl_label.configure(fg=GREEN if total_pnl >= 0 else RED)

        # Rebuild pending rows but keep the user's selection so a refresh
        # can't yank the row out from under a click.
        selected = self._selected_signal_id()
        self.pending_tree.delete(*self.pending_tree.get_children())
        now = time.time()
        for s in snap.get("pending_signals", []):
            remaining = max(0, int(s.expires_at - now))
            action = "BUY / LONG" if s.direction == "long" else "SELL / SHORT"
            self.pending_tree.insert("", "end", iid=s.signal_id, values=(
                s.signal_id, s.symbol, action, f"{s.score:+.1f}",
                f"{s.price:.6g}", f"{s.adx:.0f}", f"{remaining // 60}:{remaining % 60:02d}",
            ), tags=(s.direction,))
        if selected and self.pending_tree.exists(selected):
            self.pending_tree.selection_set(selected)
        self._on_signal_select()

        selected_trade = self._selected_trade_id()
        self.open_tree.delete(*self.open_tree.get_children())
        for t in snap["open_trades"]:
            tag = "profit" if t.unrealized_pnl >= 0 else "loss"
            self.open_tree.insert("", "end", iid=t.trade_id, values=(
                t.trade_id, t.symbol, t.side.upper(),
                f"{t.base_amount:.6g}", f"{t.entry_price:.6g}", f"{t.mark_price:.6g}",
                f"{t.unrealized_pnl:+.2f}", f"{t.stop_loss:.6g}", f"{t.take_profit:.6g}",
                time.strftime("%m-%d %H:%M", time.gmtime(t.opened_at)),
            ), tags=(tag,))
        if selected_trade and self.open_tree.exists(selected_trade):
            self.open_tree.selection_set(selected_trade)
        self._on_trade_select()

        self.closed_tree.delete(*self.closed_tree.get_children())
        for t in reversed(snap["closed_trades"]):
            pnl = t.realized_pnl or 0.0
            tag = "profit" if pnl >= 0 else "loss"
            self.closed_tree.insert("", "end", values=(
                t.symbol, t.side.upper(), f"{t.entry_price:.6g}",
                f"{t.exit_price:.6g}", f"{pnl:+.2f}", t.exit_reason,
            ), tags=(tag,))

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        for event in reversed(snap["signals"]):
            stamp = time.strftime("%H:%M:%S", time.gmtime(event.timestamp))
            self.log_text.insert("end", f"{stamp}  {event.symbol:<14} {event.text}\n")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------- settings dialog

    def _open_settings(self):
        saved = bot_config.load_credentials_file(self._credentials_path)
        has_key = bool(saved.get("api_key")) or bool(self.cfg.get("exchange", {}).get("api_key"))
        has_secret = bool(saved.get("api_secret")) or bool(
            self.cfg.get("exchange", {}).get("api_secret")
        )

        win = tk.Toplevel(self.root)
        win.title("API Key & Live Trading")
        win.configure(bg=BG)
        win.geometry("560x520")
        win.transient(self.root)
        win.grab_set()  # modal

        def label(text, **kw):
            tk.Label(win, text=text, bg=BG, fg=kw.pop("fg", FG), anchor="w",
                     justify="left", **kw).pack(fill="x", padx=18, **kw.pop("pack", {}))

        tk.Label(win, text="HTX API credentials", bg=BG, fg=ACCENT,
                 font=("TkDefaultFont", 13, "bold"), anchor="w").pack(
            fill="x", padx=18, pady=(16, 2))
        tk.Label(
            win,
            text=("Stored locally in " + self._credentials_path + " (owner-only, "
                  "gitignored). Your key never leaves this computer — never share it."),
            bg=BG, fg=DIM, anchor="w", justify="left", wraplength=520,
        ).pack(fill="x", padx=18, pady=(0, 10))

        # --- API key ---
        tk.Label(win, text="API Key (Access Key)", bg=BG, fg=FG, anchor="w").pack(
            fill="x", padx=18)
        key_var = tk.StringVar()
        key_entry = tk.Entry(win, textvariable=key_var, bg=PANEL, fg=FG,
                             insertbackground=FG, relief="flat", show="•")
        key_entry.pack(fill="x", padx=18, pady=(2, 2), ipady=4)

        # --- API secret ---
        tk.Label(win, text="API Secret (Secret Key)", bg=BG, fg=FG, anchor="w").pack(
            fill="x", padx=18, pady=(8, 0))
        secret_var = tk.StringVar()
        secret_entry = tk.Entry(win, textvariable=secret_var, bg=PANEL, fg=FG,
                                insertbackground=FG, relief="flat", show="•")
        secret_entry.pack(fill="x", padx=18, pady=(2, 2), ipady=4)

        if has_key or has_secret:
            tk.Label(win, text="A key is already saved — leave a field blank to keep it.",
                     bg=BG, fg=DIM, anchor="w").pack(fill="x", padx=18, pady=(2, 0))

        show_var = tk.BooleanVar(value=False)

        def toggle_show():
            char = "" if show_var.get() else "•"
            key_entry.configure(show=char)
            secret_entry.configure(show=char)

        tk.Checkbutton(win, text="Show characters", variable=show_var,
                       command=toggle_show, bg=BG, fg=DIM, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG,
                       anchor="w").pack(fill="x", padx=16, pady=(4, 8))

        # --- how the mode is decided (key presence) ---
        tk.Label(
            win,
            text=("MODE IS AUTOMATIC:  no API key = paper (simulation).  "
                  "An API key = REAL money."),
            bg=BG, fg=AMBER, anchor="w", justify="left", wraplength=520,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(fill="x", padx=18, pady=(2, 4))

        force_paper_now = bool(self.cfg.get("trading", {}).get("force_paper"))
        force_paper_var = tk.BooleanVar(value=force_paper_now)
        tk.Checkbutton(
            win, text="Practice mode: simulate even when a key is set (no real orders)",
            variable=force_paper_var, bg=BG, fg=FG, selectcolor=PANEL,
            activebackground=BG, activeforeground=FG, anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 4))

        mode_var = tk.StringVar()

        def resulting_mode(entered_key: bool):
            will_have_key = entered_key or has_key
            if force_paper_var.get() or not will_have_key:
                return "PAPER (simulation)"
            return "LIVE — REAL MONEY"

        def refresh_mode(*_):
            mode_var.set("On next start this will run in:  "
                         + resulting_mode(bool(key_var.get().strip())))

        key_var.trace_add("write", refresh_mode)
        force_paper_var.trace_add("write", refresh_mode)
        refresh_mode()
        tk.Label(win, textvariable=mode_var, bg=BG, fg=FG, anchor="w",
                 justify="left", wraplength=520,
                 font=("TkDefaultFont", 11, "bold")).pack(fill="x", padx=18, pady=(0, 8))

        status_var = tk.StringVar(value="")
        tk.Label(win, textvariable=status_var, bg=BG, fg=AMBER, anchor="w",
                 justify="left", wraplength=520).pack(fill="x", padx=18)

        # --- buttons ---
        btns = tk.Frame(win, bg=BG)
        btns.pack(fill="x", padx=18, pady=14, side="bottom")

        def do_test():
            if self.test_connection is None:
                status_var.set("Connection test isn't available in this build.")
                return
            api_key = key_var.get().strip() or saved.get("api_key") or \
                self.cfg.get("exchange", {}).get("api_key", "")
            api_secret = secret_var.get().strip() or saved.get("api_secret") or \
                self.cfg.get("exchange", {}).get("api_secret", "")
            if not api_key or not api_secret:
                status_var.set("Enter both key and secret first.")
                return
            status_var.set("Testing connection to HTX…")
            win.update_idletasks()
            try:
                ok, message = self.test_connection(api_key, api_secret)
            except Exception as exc:  # network / library error
                ok, message = False, str(exc)
            status_var.set(("✓ " if ok else "✗ ") + message)

        def persist() -> bool:
            """Save the form. Returns True on success, False if cancelled/failed."""
            api_key = key_var.get().strip()
            api_secret = secret_var.get().strip()
            will_be_live = resulting_mode(bool(api_key)).startswith("LIVE")
            # A key with no matching secret can't trade; guard it.
            if (api_key or has_key) and not (api_secret or has_secret):
                messagebox.showerror(
                    "API secret required",
                    "Enter your API secret to go with the API key.",
                    parent=win,
                )
                return False
            if will_be_live and not messagebox.askyesno(
                "Confirm REAL-money trading",
                "An API key is set and practice mode is off, so the bot will place "
                "REAL orders with REAL money when it (re)starts. Continue?",
                parent=win,
            ):
                return False
            updates = {
                "api_key": api_key,
                "api_secret": api_secret,
                "force_paper": bool(force_paper_var.get()),
            }
            try:
                bot_config.save_credentials(self._credentials_path, updates)
            except OSError as exc:
                messagebox.showerror("Could not save", str(exc), parent=win)
                return False
            self.state.log_signal("*", "credentials updated from Settings")
            return True

        def do_save():
            if not persist():
                return
            will_be_live = resulting_mode(bool(key_var.get().strip())).startswith("LIVE")
            messagebox.showinfo(
                "Saved",
                "Saved to " + self._credentials_path + ".\n\n"
                + ("Mode on next start: LIVE (real money). "
                   if will_be_live else "Mode on next start: paper. ")
                + "Restart the bot to apply.",
                parent=win,
            )
            win.destroy()

        def do_save_restart():
            if not persist():
                return
            if self.on_restart is None:
                messagebox.showinfo(
                    "Saved",
                    "Saved. Restart isn't available in this build — restart the "
                    "bot manually to apply.",
                    parent=win,
                )
                win.destroy()
                return
            win.destroy()
            self.restart_requested = True
            self.state.log_signal("*", "restarting to apply new settings…")
            self._handle_close()  # stops the bot and closes the window

        tk.Button(btns, text="Cancel", command=win.destroy, bg="#232734", fg=FG,
                  relief="flat", padx=14, pady=4).pack(side="right")
        tk.Button(btns, text="Save & restart", command=do_save_restart, bg="#1e5c3a",
                  fg=FG, activebackground=GREEN, activeforeground=BG, relief="flat",
                  padx=14, pady=4).pack(side="right", padx=8)
        tk.Button(btns, text="Save", command=do_save, bg="#232734", fg=FG,
                  relief="flat", padx=14, pady=4).pack(side="right", padx=8)
        tk.Button(btns, text="Test connection", command=do_test, bg="#232734", fg=FG,
                  relief="flat", padx=14, pady=4).pack(side="left")

        key_entry.focus_set()

    # ------------------------------------------------------------------ run

    def _handle_close(self):
        if self.on_close:
            self.on_close()
        self.root.destroy()

    def run(self):
        self.root.after(200, self._refresh)
        self.root.mainloop()
