import json
import queue
import threading
import time
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from tkinter import messagebox, ttk

import requests

from stock_watcher import (
    CONFIG_PATH,
    DATETIME_FORMAT,
    detect_stock_status,
    fetch_page,
    load_config,
    notify,
    now,
    open_product_page,
    send_email_notification,
)


@dataclass
class CheckEvent:
    timestamp: datetime
    status: str
    source: str
    error: str | None = None


def fmt_dt(value: datetime) -> str:
    return value.strftime(DATETIME_FORMAT)


class StockWatcherGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Stock Watcher")
        self.root.geometry("1020x760")

        self.cfg = load_config(CONFIG_PATH)
        self.last_status: str | None = None
        self.last_in_stock_at: datetime | None = None

        self.history: deque[CheckEvent] = deque(maxlen=40)
        self.queue: queue.Queue[CheckEvent] = queue.Queue()

        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

        self._build_ui()
        self._populate_settings_from_config()
        self._set_idle_state()
        self._schedule_queue_pump()

    def _read_raw_config(self) -> dict:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=14)
        main.pack(fill=tk.BOTH, expand=True)

        top_controls = ttk.Frame(main)
        top_controls.pack(fill=tk.X)

        self.start_btn = ttk.Button(top_controls, text="Start", command=self.start_monitoring)
        self.stop_btn = ttk.Button(top_controls, text="Stop", command=self.stop_monitoring)
        self.check_now_btn = ttk.Button(top_controls, text="Check Now", command=self.check_now)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.stop_btn.pack(side=tk.LEFT, padx=6)
        self.check_now_btn.pack(side=tk.LEFT, padx=6)

        status_card = ttk.LabelFrame(main, text="Current State", padding=12)
        status_card.pack(fill=tk.X, pady=(12, 8))

        self.state_var = tk.StringVar(value="Idle")
        self.last_check_var = tk.StringVar(value="-")
        self.last_in_stock_var = tk.StringVar(value="Never (this session)")
        self.next_check_var = tk.StringVar(value="-")

        self._row(status_card, 0, "Status", self.state_var)
        self._row(status_card, 1, "Last Check", self.last_check_var)
        self._row(status_card, 2, "Last In Stock", self.last_in_stock_var)
        self._row(status_card, 3, "Next Check", self.next_check_var)

        settings_card = ttk.LabelFrame(main, text="Settings", padding=10)
        settings_card.pack(fill=tk.X, pady=(4, 8))

        self.url_var = tk.StringVar()
        self.interval_var = tk.StringVar()
        self.timeout_var = tk.StringVar()
        self.selector_var = tk.StringVar()
        self.in_keywords_var = tk.StringVar()
        self.out_keywords_var = tk.StringVar()
        self.notify_every_var = tk.BooleanVar(value=False)
        self.open_browser_var = tk.BooleanVar(value=False)

        self._setting_row(settings_card, 0, "URL", self.url_var, width=95)
        self._setting_row(settings_card, 1, "Check Seconds", self.interval_var, width=14)
        self._setting_row(settings_card, 2, "Timeout Seconds", self.timeout_var, width=14)
        self._setting_row(settings_card, 3, "CSS Selector", self.selector_var, width=70)
        self._setting_row(settings_card, 4, "In-Stock Keywords", self.in_keywords_var, width=95)
        self._setting_row(settings_card, 5, "Out-Stock Keywords", self.out_keywords_var, width=95)

        flags_row = ttk.Frame(settings_card)
        flags_row.grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))
        ttk.Checkbutton(
            flags_row,
            text="Notify every in-stock check",
            variable=self.notify_every_var,
        ).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(
            flags_row,
            text="Open browser on in-stock",
            variable=self.open_browser_var,
        ).pack(side=tk.LEFT)

        email_card = ttk.LabelFrame(main, text="Email Settings", padding=10)
        email_card.pack(fill=tk.X, pady=(2, 8))

        self.email_enabled_var = tk.BooleanVar(value=False)
        self.email_smtp_server_var = tk.StringVar()
        self.email_smtp_port_var = tk.StringVar()
        self.email_ssl_var = tk.BooleanVar(value=True)
        self.email_starttls_var = tk.BooleanVar(value=False)
        self.email_username_var = tk.StringVar()
        self.email_from_var = tk.StringVar()
        self.email_to_var = tk.StringVar()
        self.email_subject_prefix_var = tk.StringVar()
        self.email_password_var = tk.StringVar()
        self.email_password_env_var = tk.StringVar()

        ttk.Checkbutton(email_card, text="Enable email notifications", variable=self.email_enabled_var).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 4)
        )
        self._setting_row(email_card, 1, "SMTP Server", self.email_smtp_server_var, width=40)
        self._setting_row(email_card, 2, "SMTP Port", self.email_smtp_port_var, width=10)

        protocol_row = ttk.Frame(email_card)
        protocol_row.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(2, 2))
        ttk.Checkbutton(protocol_row, text="Use SSL", variable=self.email_ssl_var).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(protocol_row, text="Use STARTTLS", variable=self.email_starttls_var).pack(side=tk.LEFT)

        self._setting_row(email_card, 4, "Username", self.email_username_var, width=45)
        self._setting_row(email_card, 5, "From", self.email_from_var, width=45)
        self._setting_row(email_card, 6, "To (comma-separated)", self.email_to_var, width=85)
        self._setting_row(email_card, 7, "Subject Prefix", self.email_subject_prefix_var, width=25)

        ttk.Label(email_card, text="App Password:", width=16).grid(row=8, column=0, sticky=tk.W, pady=2)
        ttk.Entry(email_card, textvariable=self.email_password_var, width=45, show="*").grid(
            row=8, column=1, sticky=tk.W, pady=2, padx=(6, 0)
        )

        self._setting_row(email_card, 9, "Password Env Name", self.email_password_env_var, width=30)

        hint = (
            "Tip: Enter App Password to save directly in config.json (no PowerShell env var needed), "
            "or set Password Env Name to keep using an environment variable."
        )
        ttk.Label(email_card, text=hint).grid(row=10, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))

        action_row = ttk.Frame(main)
        action_row.pack(fill=tk.X, pady=(2, 8))
        ttk.Button(action_row, text="Save Settings", command=self.save_settings).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(action_row, text="Reload Settings", command=self.reload_settings).pack(side=tk.LEFT)

        history_card = ttk.LabelFrame(main, text="Recent Checks", padding=10)
        history_card.pack(fill=tk.BOTH, expand=True)

        self.history_list = tk.Listbox(history_card, height=12)
        self.history_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(history_card, orient=tk.VERTICAL, command=self.history_list.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.history_list.configure(yscrollcommand=scrollbar.set)

        self.footer_var = tk.StringVar(value=f"Config: {CONFIG_PATH}")
        ttk.Label(main, textvariable=self.footer_var).pack(fill=tk.X, pady=(8, 0))

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _row(self, parent: ttk.LabelFrame, row: int, label: str, var: tk.StringVar) -> None:
        ttk.Label(parent, text=f"{label}:", width=14).grid(row=row, column=0, sticky=tk.W, pady=2)
        ttk.Label(parent, textvariable=var).grid(row=row, column=1, sticky=tk.W, pady=2)

    def _setting_row(self, parent: ttk.LabelFrame, row: int, label: str, var: tk.StringVar, width: int) -> None:
        ttk.Label(parent, text=f"{label}:", width=16).grid(row=row, column=0, sticky=tk.W, pady=2)
        ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=1, sticky=tk.EW, pady=2, padx=(6, 0))
        parent.columnconfigure(1, weight=1)

    def _populate_settings_from_config(self) -> None:
        raw = self._read_raw_config()

        self.url_var.set(self.cfg.url)
        self.interval_var.set(str(self.cfg.check_every_seconds))
        self.timeout_var.set(str(self.cfg.request_timeout_seconds))
        self.selector_var.set(self.cfg.css_selector or "")
        self.in_keywords_var.set(", ".join(self.cfg.in_stock_keywords))
        self.out_keywords_var.set(", ".join(self.cfg.out_of_stock_keywords))
        self.notify_every_var.set(self.cfg.notify_every_in_stock_check)
        self.open_browser_var.set(self.cfg.open_browser_on_in_stock)

        email_raw = raw.get("email_notifications", {})
        self.email_enabled_var.set(bool(email_raw.get("enabled", False)))
        self.email_smtp_server_var.set(email_raw.get("smtp_server", "smtp.fastmail.com"))
        self.email_smtp_port_var.set(str(email_raw.get("smtp_port", 465)))
        self.email_ssl_var.set(bool(email_raw.get("use_ssl", True)))
        self.email_starttls_var.set(bool(email_raw.get("use_starttls", False)))
        self.email_username_var.set(email_raw.get("username", ""))
        self.email_from_var.set(email_raw.get("from_address", ""))
        to_addresses = email_raw.get("to_addresses", [])
        if isinstance(to_addresses, list):
            self.email_to_var.set(", ".join(to_addresses))
        else:
            self.email_to_var.set(str(to_addresses))
        self.email_subject_prefix_var.set(email_raw.get("subject_prefix", "[Stock Alert]"))
        self.email_password_var.set("")
        self.email_password_env_var.set(email_raw.get("password_env", ""))

    def _set_idle_state(self) -> None:
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)

    def _set_running_state(self) -> None:
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)

    def _parse_keywords(self, raw_text: str) -> list[str]:
        return [s.strip() for s in raw_text.split(",") if s.strip()]

    def _parse_to_addresses(self, raw_text: str) -> list[str]:
        return [s.strip() for s in raw_text.split(",") if s.strip()]

    def save_settings(self) -> None:
        try:
            check_every = int(self.interval_var.get().strip())
            timeout = int(self.timeout_var.get().strip())
            smtp_port = int(self.email_smtp_port_var.get().strip())
        except ValueError:
            messagebox.showerror(
                "Invalid Settings",
                "Check Seconds, Timeout Seconds, and SMTP Port must be numbers.",
            )
            return

        if check_every <= 0 or timeout <= 0 or smtp_port <= 0:
            messagebox.showerror(
                "Invalid Settings",
                "Check Seconds, Timeout Seconds, and SMTP Port must be greater than zero.",
            )
            return

        in_keywords = self._parse_keywords(self.in_keywords_var.get())
        out_keywords = self._parse_keywords(self.out_keywords_var.get())
        if not in_keywords:
            messagebox.showerror("Invalid Settings", "Provide at least one in-stock keyword.")
            return

        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("Invalid Settings", "URL cannot be empty.")
            return

        selector = self.selector_var.get().strip() or None
        to_addresses = self._parse_to_addresses(self.email_to_var.get())

        raw = self._read_raw_config()
        raw["url"] = url
        raw["check_every_seconds"] = check_every
        raw["request_timeout_seconds"] = timeout
        raw["css_selector"] = selector
        raw["in_stock_keywords"] = in_keywords
        raw["out_of_stock_keywords"] = out_keywords
        raw["notify_every_in_stock_check"] = bool(self.notify_every_var.get())
        raw["open_browser_on_in_stock"] = bool(self.open_browser_var.get())

        email_raw = raw.setdefault("email_notifications", {})
        email_raw["enabled"] = bool(self.email_enabled_var.get())
        email_raw["smtp_server"] = self.email_smtp_server_var.get().strip() or "smtp.fastmail.com"
        email_raw["smtp_port"] = smtp_port
        email_raw["use_ssl"] = bool(self.email_ssl_var.get())
        email_raw["use_starttls"] = bool(self.email_starttls_var.get())
        email_raw["username"] = self.email_username_var.get().strip()
        email_raw["from_address"] = self.email_from_var.get().strip()
        email_raw["to_addresses"] = to_addresses
        email_raw["subject_prefix"] = self.email_subject_prefix_var.get().strip() or "[Stock Alert]"

        entered_password = self.email_password_var.get().strip()
        env_name = self.email_password_env_var.get().strip()
        if entered_password:
            email_raw["password"] = entered_password
            email_raw.pop("password_env", None)
        elif env_name:
            email_raw["password_env"] = env_name
            email_raw.pop("password", None)

        CONFIG_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        self.cfg = load_config(CONFIG_PATH)
        self.email_password_var.set("")
        self.footer_var.set(f"Settings saved at {now()}")

    def reload_settings(self) -> None:
        self.cfg = load_config(CONFIG_PATH)
        self._populate_settings_from_config()
        self.footer_var.set(f"Settings reloaded at {now()}")

    def start_monitoring(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        self.stop_event.clear()
        self.worker = threading.Thread(target=self._monitor_loop, daemon=True)
        self.worker.start()
        self._set_running_state()
        self.footer_var.set(f"Monitoring every {self.cfg.check_every_seconds}s")

    def stop_monitoring(self) -> None:
        self.stop_event.set()
        self._set_idle_state()
        self.next_check_var.set("-")
        self.footer_var.set("Monitoring stopped")

    def check_now(self) -> None:
        threading.Thread(target=self._single_check, daemon=True).start()

    def _single_check(self) -> None:
        event = self._run_check()
        self.queue.put(event)

    def _monitor_loop(self) -> None:
        while not self.stop_event.is_set():
            start = time.time()
            event = self._run_check()
            self.queue.put(event)

            elapsed = time.time() - start
            wait_s = max(self.cfg.check_every_seconds - elapsed, 0)
            next_time = datetime.now().timestamp() + wait_s
            self.queue.put(
                CheckEvent(
                    timestamp=datetime.fromtimestamp(next_time),
                    status="next_check",
                    source="",
                )
            )
            if self.stop_event.wait(wait_s):
                break

    def _run_check(self) -> CheckEvent:
        ts = datetime.now()
        try:
            html = fetch_page(self.cfg.url, self.cfg.request_timeout_seconds, self.cfg.user_agent)
            status, source = detect_stock_status(html, self.cfg)
            self._handle_notifications(status, source)
            return CheckEvent(timestamp=ts, status=status, source=source)
        except requests.HTTPError as exc:
            return CheckEvent(timestamp=ts, status="error", source="http", error=str(exc))
        except requests.RequestException as exc:
            return CheckEvent(timestamp=ts, status="error", source="network", error=str(exc))
        except Exception as exc:
            return CheckEvent(timestamp=ts, status="error", source="unexpected", error=str(exc))

    def _handle_notifications(self, status: str, source: str) -> None:
        should_notify = False
        if status == "in_stock":
            if self.cfg.notify_every_in_stock_check:
                should_notify = True
            elif self.last_status != "in_stock":
                should_notify = True

        if not should_notify:
            self.last_status = status
            return

        notify("Stock Alert", "Item appears to be IN STOCK. Check now.")
        if self.cfg.email_notifications and self.cfg.email_notifications.enabled:
            email_subject = "Item is in stock"
            email_body = (
                f"Detected in stock at {now()}.\n\n"
                f"URL: {self.cfg.url}\n"
                f"Detection: {source}\n"
            )
            try:
                send_email_notification(self.cfg.email_notifications, email_subject, email_body)
            except Exception as exc:
                self.queue.put(
                    CheckEvent(
                        timestamp=datetime.now(),
                        status="error",
                        source="email",
                        error=f"Email notification error: {exc}",
                    )
                )

        if self.cfg.open_browser_on_in_stock:
            open_product_page(self.cfg.url)

        self.last_status = status

    def _schedule_queue_pump(self) -> None:
        self._drain_queue()
        self.root.after(250, self._schedule_queue_pump)

    def _drain_queue(self) -> None:
        while True:
            try:
                event = self.queue.get_nowait()
            except queue.Empty:
                break

            if event.status == "next_check":
                self.next_check_var.set(fmt_dt(event.timestamp))
                continue

            self.history.appendleft(event)
            self._render_event(event)
            self._render_history()

    def _render_event(self, event: CheckEvent) -> None:
        t = fmt_dt(event.timestamp)
        self.last_check_var.set(t)

        if event.status == "in_stock":
            self.state_var.set("IN STOCK")
            self.last_in_stock_at = event.timestamp
            self.last_in_stock_var.set(t)
        elif event.status == "out_of_stock":
            self.state_var.set("Out of stock")
        elif event.status == "unknown":
            self.state_var.set("Unknown")
        elif event.status == "error":
            self.state_var.set("Error")
            self.footer_var.set(event.error or "Unknown error")

        if event.status != "error":
            self.footer_var.set(f"Last source: {event.source}")

    def _render_history(self) -> None:
        self.history_list.delete(0, tk.END)
        for item in self.history:
            t = fmt_dt(item.timestamp)
            if item.status == "error":
                line = f"[{t}] ERROR ({item.source}) {item.error}"
            else:
                line = f"[{t}] {item.status} ({item.source})"
            self.history_list.insert(tk.END, line)

    def on_close(self) -> None:
        self.stop_monitoring()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    StockWatcherGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
