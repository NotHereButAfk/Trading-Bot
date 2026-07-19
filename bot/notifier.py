"""Email notifications for bot lifecycle and trade events."""

import logging
import smtplib
import threading
import time
from email.mime.text import MIMEText

log = logging.getLogger("bot.notifier")


class EmailNotifier:
    """SMTP notifier; sends run on a background thread so trading never blocks."""

    def __init__(self, cfg: dict):
        self.cfg = cfg["email"]
        self.enabled = bool(self.cfg["enabled"])

    def _send(self, subject: str, body: str):
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = self.cfg["from_addr"] or self.cfg["smtp_user"]
            msg["To"] = ", ".join(self.cfg["to_addrs"])
            with smtplib.SMTP(self.cfg["smtp_host"], self.cfg["smtp_port"], timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(self.cfg["smtp_user"], self.cfg["smtp_password"])
                smtp.sendmail(
                    msg["From"], list(self.cfg["to_addrs"]), msg.as_string()
                )
            log.info("email sent: %s", subject)
        except Exception:
            log.exception("failed to send email: %s", subject)

    def send(self, subject: str, body: str):
        if not self.enabled:
            log.debug("email disabled, skipping: %s", subject)
            return
        threading.Thread(target=self._send, args=(subject, body), daemon=True).start()

    # ------------------------------------------------------------ templates

    def notify_startup(self, mode: str, symbols: list, timeframe: str, equity: float):
        body = (
            "HTX futures trading bot started.\n\n"
            f"Mode:      {mode}\n"
            f"Symbols:   {', '.join(symbols)}\n"
            f"Timeframe: {timeframe}\n"
            f"Equity:    {equity:.2f} USDT\n"
            f"Time:      {_now()}\n"
        )
        self.send("[Trading Bot] Started", body)

    def notify_open(self, trade, signal_score: float, reasons: list):
        body = (
            f"OPENED {trade.side.upper()} {trade.symbol}\n\n"
            f"Entry price:  {trade.entry_price:.6g} USDT\n"
            f"Size:         {trade.base_amount:.6g} ({trade.notional:.2f} USDT notional)\n"
            f"Leverage:     {trade.leverage}x\n"
            f"Stop loss:    {trade.stop_loss:.6g}\n"
            f"Take profit:  {trade.take_profit:.6g}\n"
            f"Signal score: {signal_score}\n"
            f"Reasons:      {'; '.join(reasons)}\n"
            f"Time:         {_now()}\n"
        )
        self.send(
            f"[Trading Bot] OPEN {trade.side.upper()} {trade.symbol} @ {trade.entry_price:.6g}",
            body,
        )

    def notify_close(self, trade):
        pnl = trade.realized_pnl or 0.0
        outcome = "PROFIT" if pnl >= 0 else "LOSS"
        body = (
            f"CLOSED {trade.side.upper()} {trade.symbol} ({outcome})\n\n"
            f"Entry price: {trade.entry_price:.6g} USDT\n"
            f"Exit price:  {trade.exit_price:.6g} USDT\n"
            f"Size:        {trade.base_amount:.6g}\n"
            f"Realized PnL: {pnl:+.2f} USDT\n"
            f"Reason:      {trade.exit_reason}\n"
            f"Time:        {_now()}\n"
        )
        self.send(
            f"[Trading Bot] CLOSE {trade.symbol} {pnl:+.2f} USDT ({trade.exit_reason})",
            body,
        )

    def notify_error(self, message: str):
        self.send("[Trading Bot] ERROR", f"{message}\n\nTime: {_now()}")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
